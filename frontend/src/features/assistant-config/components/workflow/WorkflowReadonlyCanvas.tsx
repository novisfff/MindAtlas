import { memo, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BaseEdge,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { Maximize2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { getNodeOutputHandleIds } from './workflowGeometry'
import { resolveWorkflowNodeTone } from './workflowNodeVisuals'
import {
  buildThumbnailPreviewScene,
  type ThumbnailPreviewDensity,
} from './workflowReadonlyPreviewLayout'
import '@xyflow/react/dist/style.css'

type ReadonlyCanvasVariant = 'thumbnail' | 'canvas'

type ReadonlyCanvasNodeData = {
  label: string
  nodeType: string
  variant: ReadonlyCanvasVariant
  density?: ThumbnailPreviewDensity
  frame?: { width: number; height: number }
  outputHandles: string[]
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

type ThumbnailPreviewEdgeData = {
  density: ThumbnailPreviewDensity
  sourceSiblingCount: number
  targetSiblingCount: number
}

const CANVAS_EDGE_STYLE = {
  stroke: 'hsl(var(--muted-foreground) / 0.55)',
  strokeWidth: 2.4,
  strokeLinecap: 'round' as const,
}

const THUMBNAIL_EDGE_VISUALS: Record<ThumbnailPreviewDensity, {
  style: {
    stroke: string
    strokeWidth: number
    strokeLinecap: 'round'
  }
  fitPadding: number
  maxZoom: number
  turnRadius: number
  straightThreshold: number
  branchStub: number
  defaultStub: number
}> = {
  regular: {
    style: {
      stroke: 'rgba(148, 163, 184, 0.62)',
      strokeWidth: 1.45,
      strokeLinecap: 'round',
    },
    fitPadding: 0.06,
    maxZoom: 1.08,
    turnRadius: 8,
    straightThreshold: 4,
    branchStub: 8,
    defaultStub: 18,
  },
  compact: {
    style: {
      stroke: 'rgba(148, 163, 184, 0.58)',
      strokeWidth: 1.22,
      strokeLinecap: 'round',
    },
    fitPadding: 0.045,
    maxZoom: 1.28,
    turnRadius: 6,
    straightThreshold: 3,
    branchStub: 5,
    defaultStub: 12,
  },
  dense: {
    style: {
      stroke: 'rgba(148, 163, 184, 0.45)',
      strokeWidth: 0.6,
      strokeLinecap: 'round',
    },
    fitPadding: 0.03,
    maxZoom: 1.78,
    turnRadius: 2,
    straightThreshold: 2,
    branchStub: 2,
    defaultStub: 4,
  },
}

type OrthogonalPoint = {
  x: number
  y: number
}

function normalizeOrthogonalPoints(points: OrthogonalPoint[]): OrthogonalPoint[] {
  const deduped = points.filter((point, index) => {
    const previous = points[index - 1]
    return !previous || previous.x !== point.x || previous.y !== point.y
  })
  if (deduped.length <= 2) return deduped

  const normalized: OrthogonalPoint[] = [deduped[0]!]
  for (let index = 1; index < deduped.length - 1; index += 1) {
    const previous = normalized[normalized.length - 1]!
    const current = deduped[index]!
    const next = deduped[index + 1]!
    const collinearX = previous.x === current.x && current.x === next.x
    const collinearY = previous.y === current.y && current.y === next.y
    if (collinearX || collinearY) {
      continue
    }
    normalized.push(current)
  }
  normalized.push(deduped[deduped.length - 1]!)
  return normalized
}

function buildRoundedOrthogonalPath(
  points: OrthogonalPoint[],
  radius: number,
): string {
  const normalized = normalizeOrthogonalPoints(points)
  if (normalized.length === 0) return ''
  if (normalized.length === 1) return `M ${normalized[0]!.x} ${normalized[0]!.y}`
  if (normalized.length === 2) {
    return `M ${normalized[0]!.x} ${normalized[0]!.y} L ${normalized[1]!.x} ${normalized[1]!.y}`
  }

  let path = `M ${normalized[0]!.x} ${normalized[0]!.y}`

  for (let index = 1; index < normalized.length - 1; index += 1) {
    const previous = normalized[index - 1]!
    const current = normalized[index]!
    const next = normalized[index + 1]!
    const incomingX = Math.sign(current.x - previous.x)
    const incomingY = Math.sign(current.y - previous.y)
    const outgoingX = Math.sign(next.x - current.x)
    const outgoingY = Math.sign(next.y - current.y)
    const incomingDistance = Math.abs(current.x - previous.x) + Math.abs(current.y - previous.y)
    const outgoingDistance = Math.abs(next.x - current.x) + Math.abs(next.y - current.y)
    const cornerRadius = Math.max(0, Math.min(radius, incomingDistance / 2, outgoingDistance / 2))

    const entryPoint = {
      x: current.x - incomingX * cornerRadius,
      y: current.y - incomingY * cornerRadius,
    }
    const exitPoint = {
      x: current.x + outgoingX * cornerRadius,
      y: current.y + outgoingY * cornerRadius,
    }

    path += ` L ${entryPoint.x} ${entryPoint.y}`
    if (cornerRadius > 0) {
      path += ` Q ${current.x} ${current.y} ${exitPoint.x} ${exitPoint.y}`
    } else {
      path += ` L ${current.x} ${current.y}`
    }
  }

  const lastPoint = normalized[normalized.length - 1]!
  path += ` L ${lastPoint.x} ${lastPoint.y}`
  return path
}

function buildThumbnailEdgePath({
  sourceX,
  sourceY,
  targetX,
  targetY,
  density,
  sourceSiblingCount,
  targetSiblingCount,
}: {
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
  density: ThumbnailPreviewDensity
  sourceSiblingCount: number
  targetSiblingCount: number
}): string {
  const metrics = THUMBNAIL_EDGE_VISUALS[density]
  const xGap = targetX - sourceX
  const yGap = targetY - sourceY
  const sourceIsBranching = sourceSiblingCount > 1
  const targetIsMerging = targetSiblingCount > 1

  if (Math.abs(yGap) <= metrics.straightThreshold && xGap > 0) {
    return `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`
  }

  if (xGap <= 0) {
    const middleX = Math.round((sourceX + targetX) / 2)
    return buildRoundedOrthogonalPath([
      { x: sourceX, y: sourceY },
      { x: middleX, y: sourceY },
      { x: middleX, y: targetY },
      { x: targetX, y: targetY },
    ], metrics.turnRadius)
  }

  const maxStub = Math.max(2, Math.floor(xGap / 2) - 1)
  const sourceStub = Math.min(sourceIsBranching ? metrics.branchStub : metrics.defaultStub, maxStub)
  const targetStub = Math.min(targetIsMerging ? metrics.branchStub : metrics.defaultStub, maxStub)
  const splitX = sourceX + sourceStub
  const joinX = targetX - targetStub

  if (sourceIsBranching && targetIsMerging && splitX < joinX - 2) {
    const middleY = Math.round((sourceY + targetY) / 2)
    return buildRoundedOrthogonalPath([
      { x: sourceX, y: sourceY },
      { x: splitX, y: sourceY },
      { x: splitX, y: middleY },
      { x: joinX, y: middleY },
      { x: joinX, y: targetY },
      { x: targetX, y: targetY },
    ], metrics.turnRadius)
  }

  if (sourceIsBranching) {
    return buildRoundedOrthogonalPath([
      { x: sourceX, y: sourceY },
      { x: splitX, y: sourceY },
      { x: splitX, y: targetY },
      { x: targetX, y: targetY },
    ], metrics.turnRadius)
  }

  if (targetIsMerging) {
    return buildRoundedOrthogonalPath([
      { x: sourceX, y: sourceY },
      { x: joinX, y: sourceY },
      { x: joinX, y: targetY },
      { x: targetX, y: targetY },
    ], metrics.turnRadius)
  }

  const middleX = Math.round((sourceX + targetX) / 2)
  return buildRoundedOrthogonalPath([
    { x: sourceX, y: sourceY },
    { x: middleX, y: sourceY },
    { x: middleX, y: targetY },
    { x: targetX, y: targetY },
  ], metrics.turnRadius)
}

function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const element = ref.current
    if (!element) return

    const updateSize = () => {
      const next = {
        width: Math.round(element.clientWidth),
        height: Math.round(element.clientHeight),
      }
      setSize((prev) => (
        prev.width === next.width && prev.height === next.height
          ? prev
          : next
      ))
    }

    updateSize()

    if (typeof ResizeObserver === 'undefined') {
      return undefined
    }

    const observer = new ResizeObserver(() => updateSize())
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, size] as const
}

function resolveCanvasHandleTop(index: number, total: number): number {
  if (total <= 1) return 28
  const startTop = total >= 3 ? 18 : 20
  const step = total >= 4 ? 18 : 22
  return startTop + index * step
}

function resolveThumbnailHandleTop(
  density: ThumbnailPreviewDensity,
  frameHeight: number,
  index: number,
  total: number,
): number {
  if (density === 'dense') return Math.round(frameHeight / 2)
  if (total <= 1) return Math.round(frameHeight / 2)
  const inset = density === 'compact' ? 6 : 8
  const usableHeight = Math.max(1, frameHeight - inset * 2)
  return Math.round(inset + usableHeight * ((index + 0.5) / total))
}

function resolveHandleTop(
  variant: ReadonlyCanvasVariant,
  density: ThumbnailPreviewDensity | undefined,
  frameHeight: number | undefined,
  index: number,
  total: number,
): number {
  if (variant === 'thumbnail') {
    return resolveThumbnailHandleTop(density ?? 'regular', frameHeight ?? 40, index, total)
  }
  return resolveCanvasHandleTop(index, total)
}

function ReadonlyCanvasNode({ data }: NodeProps<Node<ReadonlyCanvasNodeData>>) {
  const { t } = useTranslation()
  const nodeData = data as ReadonlyCanvasNodeData
  const label = String(nodeData.label ?? '').trim() || t(`settings.skills.nodeTypes.${nodeData.nodeType}`, { defaultValue: nodeData.nodeType })
  const nodeTypeLabel = t(`settings.skills.nodeTypes.${nodeData.nodeType}`, { defaultValue: nodeData.nodeType })
  const tone = resolveWorkflowNodeTone(nodeData.nodeType)
  const outputHandles = nodeData.outputHandles
  const isThumbnail = nodeData.variant === 'thumbnail'
  const density = nodeData.density ?? 'regular'
  const frame = nodeData.frame
  const accessibleLabel = `${nodeTypeLabel}: ${label}`

  const inputTop = resolveHandleTop(nodeData.variant, density, frame?.height, 0, 1)

  if (isThumbnail) {
    const isDense = density === 'dense'
    const isCompact = density === 'compact'
    const blockClass = isDense
      ? tone.thumbnailDenseClass
      : cn(
        tone.thumbnailSurfaceClass,
        tone.thumbnailBorderClass,
        'bg-white',
      )

    return (
      <div
        title={accessibleLabel}
        aria-label={accessibleLabel}
        className={cn(
          'relative overflow-hidden transition-all duration-200',
          isDense
            ? 'rounded-[4px] border-[0.5px] shadow-[0_1px_2px_rgba(15,23,42,0.04)] bg-[rgba(255,255,255,0.85)] backdrop-blur-[2px]'
            : 'border rounded-[15px] shadow-[0_6px_16px_rgba(15,23,42,0.05)] ring-1 ring-white/70',
          blockClass,
          nodeData.highlighted ? 'ring-2 ring-blue-300/65 shadow-[0_0_0_3px_rgba(59,130,246,0.06)]' : undefined,
        )}
        style={frame ? { width: frame.width, height: frame.height } : undefined}
      >
        <Handle
          type="target"
          position={Position.Left}
          id="input"
          className="!h-[1px] !w-[1px] !min-w-0 !min-h-0 !border-0 !opacity-0 !pointer-events-none"
          style={{ top: inputTop, left: 0 }}
        />
        {!isDense ? (
          <>
            <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(255,255,255,1),rgba(248,250,252,1))]" />
            <div className={cn('absolute inset-x-0 top-0', isCompact ? 'h-1' : 'h-1.5', tone.thumbnailAccentClass)} />
            <div className={cn('relative min-w-0', isCompact ? 'px-2.5 py-2' : 'px-3 py-2.5')}>
              {density === 'regular' ? (
                <div className="truncate text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                  {nodeTypeLabel}
                </div>
              ) : null}
              <div
                className={cn(
                  'truncate text-slate-800',
                  density === 'regular'
                    ? 'mt-1 text-[12px] font-semibold leading-4'
                    : 'pt-0.5 text-[11px] font-semibold leading-4',
                )}
              >
                {label}
              </div>
            </div>
          </>
        ) : null}
        {outputHandles.map((handleId, index) => (
          <Handle
            key={handleId}
            type="source"
            position={Position.Right}
            id={handleId}
            className="!h-[1px] !w-[1px] !min-w-0 !min-h-0 !border-0 !opacity-0 !pointer-events-none"
            style={{
              top: resolveHandleTop(nodeData.variant, density, frame?.height, index, outputHandles.length),
              right: 0,
            }}
          />
        ))}
      </div>
    )
  }

  return (
    <div
      title={accessibleLabel}
      aria-label={accessibleLabel}
      className={cn(
        'relative overflow-hidden rounded-2xl border bg-white px-4 py-3 shadow-[0_6px_18px_rgba(15,23,42,0.06)] ring-1 ring-white/70 transition-colors',
        'min-w-[180px] max-w-[240px]',
        nodeData.highlighted
          ? 'border-blue-300 bg-blue-50/90 shadow-[0_0_0_2px_rgba(59,130,246,0.14)]'
          : tone.thumbnailBorderClass,
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        className="!h-2.5 !w-2.5 !opacity-0 !pointer-events-none"
        style={{ top: inputTop }}
      />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(255,255,255,1),rgba(248,250,252,1))]" />
      <div className={cn('absolute inset-y-0 left-0 w-1', tone.thumbnailAccentClass)} />
      <div className="relative pl-2">
        <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">
          {nodeTypeLabel}
        </div>
        <div className="mt-1.5 text-[13px] font-semibold leading-5 text-slate-800 line-clamp-2">
          {label}
        </div>
        {nodeData.highlighted ? (
          <div className="mt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-700">
            {t('settings.skills.workflowCopilot.previewAffectedNode')}
          </div>
        ) : null}
      </div>
      {outputHandles.map((handleId, index) => (
        <Handle
          key={handleId}
          type="source"
          position={Position.Right}
          id={handleId}
          className="!h-2.5 !w-2.5 !opacity-0 !pointer-events-none"
          style={{ top: resolveHandleTop(nodeData.variant, density, frame?.height, index, outputHandles.length) }}
        />
      ))}
    </div>
  )
}

function ThumbnailPreviewEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  style,
  data,
}: EdgeProps) {
  const edgeData = (data ?? null) as ThumbnailPreviewEdgeData | null
  const density = edgeData?.density ?? 'regular'
  const edgePath = buildThumbnailEdgePath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    density,
    sourceSiblingCount: edgeData?.sourceSiblingCount ?? 1,
    targetSiblingCount: edgeData?.targetSiblingCount ?? 1,
  })

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      style={{
        ...(style ?? {}),
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
      }}
      interactionWidth={12}
    />
  )
}

const nodeTypes = {
  readonlyPreview: ReadonlyCanvasNode,
}

const edgeTypes = {
  thumbnailPreview: ThumbnailPreviewEdge,
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

function AutoFitViewport({
  enabled,
  padding,
  maxZoom,
  signature,
}: {
  enabled: boolean
  padding: number
  maxZoom: number
  signature: string
}) {
  const { fitView } = useReactFlow()

  useEffect(() => {
    if (!enabled) return
    const raf = requestAnimationFrame(() => {
      void fitView({
        padding,
        duration: 0,
        maxZoom,
        minZoom: 0.1,
      })
    })
    return () => cancelAnimationFrame(raf)
  }, [enabled, fitView, maxZoom, padding, signature])

  return null
}

function WorkflowReadonlyCanvasInner({
  nodes,
  edges,
  highlightedNodeIds = [],
  variant = 'canvas',
  className,
  showFitViewControl = false,
}: WorkflowReadonlyCanvasProps) {
  const [containerRef, viewportSize] = useElementSize<HTMLDivElement>()
  const highlightedSet = useMemo(() => new Set(highlightedNodeIds), [highlightedNodeIds])
  const thumbnailScene = useMemo(
    () => (
      variant === 'thumbnail'
        ? buildThumbnailPreviewScene(nodes, edges, viewportSize)
        : null
    ),
    [edges, nodes, variant, viewportSize],
  )

  const previewNodes = useMemo<Node<ReadonlyCanvasNodeData>[]>(() => {
    if (variant === 'thumbnail' && thumbnailScene) {
      return thumbnailScene.nodes.map((node) => ({
        id: node.id,
        type: 'readonlyPreview',
        position: node.position,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        style: {
          width: node.frame.width,
          height: node.frame.height,
        },
        data: {
          label: node.label,
          nodeType: node.nodeType,
          variant,
          density: thumbnailScene.density,
          frame: node.frame,
          outputHandles: node.outputHandles,
          highlighted: highlightedSet.has(node.id),
        },
      }))
    }

    return nodes.map((node) => ({
      id: node.id,
      type: 'readonlyPreview',
      position: node.position,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: String(node.data.label ?? ''),
        nodeType: String(node.data.nodeType ?? node.type ?? 'llm'),
        variant,
        outputHandles: getNodeOutputHandleIds(
          (node.data.nodeType ?? node.type ?? 'llm') as WfNodeData['nodeType'],
          (node.data.config ?? null) as Record<string, unknown> | null,
        ),
        highlighted: highlightedSet.has(node.id),
      },
    }))
  }, [highlightedSet, nodes, thumbnailScene, variant])

  const previewEdges = useMemo<Edge[]>(() => {
    const edgeVisual = variant === 'thumbnail'
      ? THUMBNAIL_EDGE_VISUALS[thumbnailScene?.density ?? 'regular']
      : null
    const sourceCounts = new Map<string, number>()
    const targetCounts = new Map<string, number>()

    edges.forEach((edge) => {
      sourceCounts.set(edge.source, (sourceCounts.get(edge.source) ?? 0) + 1)
      targetCounts.set(edge.target, (targetCounts.get(edge.target) ?? 0) + 1)
    })

    return edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
      type: variant === 'thumbnail' ? 'thumbnailPreview' : 'smoothstep',
      animated: false,
      style: edgeVisual?.style ?? CANVAS_EDGE_STYLE,
      pathOptions: variant === 'thumbnail'
        ? undefined
        : {
          borderRadius: 18,
          offset: 18,
        },
      data: variant === 'thumbnail'
        ? {
          density: thumbnailScene?.density ?? 'regular',
          sourceSiblingCount: sourceCounts.get(edge.source) ?? 1,
          targetSiblingCount: targetCounts.get(edge.target) ?? 1,
        } satisfies ThumbnailPreviewEdgeData
        : undefined,
    }))
  }, [edges, thumbnailScene?.density, variant])

  const fitPadding = variant === 'thumbnail'
    ? THUMBNAIL_EDGE_VISUALS[thumbnailScene?.density ?? 'regular'].fitPadding
    : 0.16
  const fitMaxZoom = variant === 'thumbnail'
    ? THUMBNAIL_EDGE_VISUALS[thumbnailScene?.density ?? 'regular'].maxZoom
    : 1

  const sceneSignature = useMemo(() => {
    const nodePart = previewNodes
      .map((node) => `${node.id}:${Math.round(node.position.x)}:${Math.round(node.position.y)}`)
      .join('|')
    const edgePart = previewEdges
      .map((edge) => `${edge.source}:${edge.sourceHandle ?? ''}->${edge.target}:${edge.targetHandle ?? ''}`)
      .join('|')
    return `${variant}::${nodePart}::${edgePart}::${fitPadding}::${fitMaxZoom}`
  }, [fitMaxZoom, fitPadding, previewEdges, previewNodes, variant])

  return (
    <div ref={containerRef} className={cn('h-full min-h-0 min-w-0 w-full', className)}>
      <ReactFlowProvider>
        <ReactFlow
          className={cn(
            'h-full w-full',
            variant === 'thumbnail' ? 'bg-transparent' : undefined,
          )}
          nodes={previewNodes}
          edges={previewEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: fitPadding, maxZoom: fitMaxZoom }}
          defaultEdgeOptions={{
            type: variant === 'thumbnail' ? 'thumbnailPreview' : 'smoothstep',
            animated: false,
            style: variant === 'thumbnail'
              ? THUMBNAIL_EDGE_VISUALS[thumbnailScene?.density ?? 'regular'].style
              : CANVAS_EDGE_STYLE,
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
          {variant === 'canvas' ? <Background gap={16} size={1} color="#cbd5e1" /> : null}
          <AutoFitViewport enabled={previewNodes.length > 0} padding={fitPadding} maxZoom={fitMaxZoom} signature={sceneSignature} />
          {showFitViewControl && variant === 'canvas' ? <FitViewControl /> : null}
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  )
}

export const WorkflowReadonlyCanvas = memo(WorkflowReadonlyCanvasInner)
