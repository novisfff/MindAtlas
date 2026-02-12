import { memo, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { ArrowRight, Workflow } from 'lucide-react'
import type { AssistantSkill } from '../../api/skills'
import { deserializeFromSkill } from './serialization'
import '@xyflow/react/dist/style.css'

type PreviewNodeData = {
  label: string
  nodeType: string
}

type WorkflowReadonlyPreviewProps = {
  skill: AssistantSkill
  onOpenEditor: () => void
}

function PreviewNode({ data }: NodeProps<Node<PreviewNodeData>>) {
  const nodeData = data as PreviewNodeData
  return (
    <div className="relative min-w-[96px] max-w-[132px] rounded-md border bg-background/95 px-2 py-1 shadow-sm">
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        className="!h-2 !w-2 !opacity-0 !pointer-events-none"
      />
      <div className="text-[9px] text-muted-foreground uppercase tracking-wide">{nodeData.nodeType}</div>
      <div className="text-[11px] font-medium truncate">{nodeData.label}</div>
      <Handle
        type="source"
        position={Position.Right}
        id="output"
        className="!h-2 !w-2 !opacity-0 !pointer-events-none"
      />
    </div>
  )
}

function WorkflowReadonlyPreviewInner({ skill, onOpenEditor }: WorkflowReadonlyPreviewProps) {
  const { t } = useTranslation()

  const { nodes, edges } = useMemo(() => {
    const hasNodes = Array.isArray(skill.nodes) && skill.nodes.length > 0
    if (!hasNodes) {
      return { nodes: [] as Node<PreviewNodeData>[], edges: [] as Edge[] }
    }

    try {
      const parsed = deserializeFromSkill(skill)
      const minX = parsed.nodes.reduce((acc, n) => Math.min(acc, n.position.x), Number.POSITIVE_INFINITY)
      const minY = parsed.nodes.reduce((acc, n) => Math.min(acc, n.position.y), Number.POSITIVE_INFINITY)
      const scaleX = 0.62
      const scaleY = 0.72
      const offsetX = 24
      const offsetY = 16

      const previewNodes: Node<PreviewNodeData>[] = parsed.nodes.map((n) => {
        const label = n.data?.label || n.type || n.id
        const nodeType = n.data?.nodeType || n.type || 'node'
        return {
          id: n.id,
          type: 'preview',
          position: {
            x: Math.round((n.position.x - minX) * scaleX + offsetX),
            y: Math.round((n.position.y - minY) * scaleY + offsetY),
          },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            label: String(label),
            nodeType: String(nodeType),
          },
        }
      })

      const previewEdges: Edge[] = parsed.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: 'output',
        targetHandle: 'input',
        type: 'bezier',
        animated: false,
        style: {
          stroke: 'hsl(var(--muted-foreground) / 0.6)',
          strokeWidth: 2.2,
          strokeLinecap: 'round',
        },
      }))

      return { nodes: previewNodes, edges: previewEdges }
    } catch {
      return { nodes: [] as Node<PreviewNodeData>[], edges: [] as Edge[] }
    }
  }, [skill])

  const isEmpty = nodes.length === 0
  const nodeTypes = useMemo(() => ({ preview: PreviewNode }), [])

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpenEditor}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpenEditor()
        }
      }}
      className="w-full rounded-xl border border-primary/20 bg-primary/5 p-4 cursor-pointer hover:border-primary/40 hover:bg-primary/10 transition-colors"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-primary">
          <Workflow className="w-4 h-4" />
          <span className="text-sm font-semibold">{t('settings.skills.workflowPreviewTitle')}</span>
        </div>
        <ArrowRight className="w-4 h-4 text-primary/70" />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t('settings.skills.workflowPreviewDesc')}</p>

      {isEmpty ? (
        <div className="mt-3 rounded-lg border border-dashed bg-background/50 px-3 py-6 text-center text-xs text-muted-foreground">
          {t('settings.skills.workflowPreviewEmpty')}
        </div>
      ) : (
        <div className="mt-3 h-56 rounded-lg border bg-background overflow-hidden">
          <ReactFlowProvider>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.08 }}
              defaultEdgeOptions={{
                type: 'bezier',
                animated: false,
                style: {
                  stroke: 'hsl(var(--muted-foreground) / 0.6)',
                  strokeWidth: 2.2,
                  strokeLinecap: 'round',
                },
              }}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              zoomOnScroll={false}
              zoomOnPinch={false}
              panOnDrag={false}
              preventScrolling
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={16} size={1} />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
      )}

      <div className="mt-3 text-xs font-medium text-primary">{t('settings.skills.workflowPreviewOpen')}</div>
    </div>
  )
}

export const WorkflowReadonlyPreview = memo(WorkflowReadonlyPreviewInner)
