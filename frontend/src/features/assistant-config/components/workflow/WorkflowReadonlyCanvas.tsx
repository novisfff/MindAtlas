import { memo, useMemo } from 'react'
import {
  Background,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { Maximize2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import '@xyflow/react/dist/style.css'

type ReadonlyCanvasVariant = 'thumbnail' | 'canvas'

type ReadonlyCanvasNodeData = {
  label: string
  nodeType: string
  compact: boolean
  highlighted: boolean
}

interface WorkflowReadonlyCanvasProps {
  nodes: Node<WfNodeData>[]
  edges: Edge[]
  highlightedNodeIds?: string[]
  variant?: ReadonlyCanvasVariant
  className?: string
  showFitViewControl?: boolean
}

const edgeStyle = {
  stroke: 'hsl(var(--muted-foreground) / 0.55)',
  strokeWidth: 2.4,
  strokeLinecap: 'round' as const,
}

function previewHandleTop(compact: boolean): number {
  return compact ? 20 : 28
}

function ReadonlyCanvasNode({ data }: NodeProps<Node<ReadonlyCanvasNodeData>>) {
  const { t } = useTranslation()
  const nodeData = data as ReadonlyCanvasNodeData
  const compact = nodeData.compact
  const label = nodeData.label.trim() || t(`settings.skills.nodeTypes.${nodeData.nodeType}`, { defaultValue: nodeData.nodeType })
  const nodeTypeLabel = t(`settings.skills.nodeTypes.${nodeData.nodeType}`, { defaultValue: nodeData.nodeType })
  const highlightClass = nodeData.highlighted
    ? 'border-blue-300 bg-blue-50/90 shadow-[0_0_0_2px_rgba(59,130,246,0.14)]'
    : 'border-slate-200/90 bg-white/95'

  return (
    <div
      className={[
        'relative rounded-2xl border shadow-sm transition-colors',
        compact ? 'min-w-[110px] max-w-[156px] px-2.5 py-2' : 'min-w-[180px] max-w-[240px] px-4 py-3',
        highlightClass,
      ].join(' ')}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        className="!h-2.5 !w-2.5 !opacity-0 !pointer-events-none"
        style={{ top: previewHandleTop(compact) }}
      />
      <div className={compact ? 'text-[9px] uppercase tracking-[0.12em] text-slate-400' : 'text-[10px] uppercase tracking-[0.16em] text-slate-400'}>
        {nodeTypeLabel}
      </div>
      <div className={compact ? 'mt-1 text-[11px] font-medium leading-4 text-slate-800 line-clamp-2' : 'mt-1.5 text-[13px] font-semibold leading-5 text-slate-800 line-clamp-2'}>
        {label}
      </div>
      {nodeData.highlighted && (
        <div className={compact ? 'mt-1.5 text-[9px] font-medium text-blue-700' : 'mt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-700'}>
          {t('settings.skills.workflowCopilot.previewAffectedNode')}
        </div>
      )}
      {nodeData.nodeType !== 'output' && (
        <Handle
          type="source"
          position={Position.Right}
          id="output"
          className="!h-2.5 !w-2.5 !opacity-0 !pointer-events-none"
          style={{ top: previewHandleTop(compact) }}
        />
      )}
    </div>
  )
}

const nodeTypes = {
  readonlyPreview: ReadonlyCanvasNode,
}

function FitViewControl() {
  const { fitView } = useReactFlow()
  const { t } = useTranslation()

  return (
    <Panel position="bottom-left">
      <button
        type="button"
        onClick={() => {
          void fitView({ padding: 0.12, duration: 180 })
        }}
        className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white/92 px-3 py-2 text-xs font-medium text-slate-700 shadow-sm transition-colors hover:bg-white"
        title={t('common.fitView')}
      >
        <Maximize2 className="h-3.5 w-3.5" />
        {t('common.fitView')}
      </button>
    </Panel>
  )
}

function WorkflowReadonlyCanvasInner({
  nodes,
  edges,
  highlightedNodeIds = [],
  variant = 'canvas',
  className,
  showFitViewControl = false,
}: WorkflowReadonlyCanvasProps) {
  const highlightedSet = useMemo(() => new Set(highlightedNodeIds), [highlightedNodeIds])
  const compact = variant === 'thumbnail'

  const previewNodes = useMemo<Node<ReadonlyCanvasNodeData>[]>(() => (
    nodes.map((node) => ({
      id: node.id,
      type: 'readonlyPreview',
      position: node.position,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: String(node.data.label ?? ''),
        nodeType: String(node.data.nodeType ?? node.type ?? 'node'),
        compact,
        highlighted: highlightedSet.has(node.id),
      },
    }))
  ), [compact, highlightedSet, nodes])

  const previewEdges = useMemo<Edge[]>(() => (
    edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
      type: 'smoothstep',
      animated: false,
      style: edgeStyle,
      pathOptions: {
        borderRadius: 18,
        offset: 18,
      },
    }))
  ), [edges])

  return (
    <div className={cn('h-full min-h-0 min-w-0 w-full', className)}>
      <ReactFlowProvider>
        <ReactFlow
          className="h-full w-full"
          nodes={previewNodes}
          edges={previewEdges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: compact ? 0.12 : 0.16 }}
          defaultEdgeOptions={{
            type: 'smoothstep',
            animated: false,
            style: edgeStyle,
          }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          panOnDrag={false}
          panOnScroll={false}
          preventScrolling
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} size={1} color="#cbd5e1" />
          {showFitViewControl ? <FitViewControl /> : null}
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  )
}

export const WorkflowReadonlyCanvas = memo(WorkflowReadonlyCanvasInner)
