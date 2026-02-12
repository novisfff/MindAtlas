import { useMemo, useState } from 'react'
import { X } from 'lucide-react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'

type WorkflowDeletableEdgeData = {
  onDelete?: (edgeId: string) => void
  onSelect?: (edgeId: string) => void
}

const BASE_STROKE = 'hsl(var(--muted-foreground) / 0.55)'
const ACTIVE_STROKE = 'hsl(var(--primary) / 0.75)'

export function WorkflowDeletableEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  data,
}: EdgeProps) {
  const [hovered, setHovered] = useState(false)
  const edgeData = (data ?? null) as WorkflowDeletableEdgeData | null
  const showDelete = hovered || selected

  const [edgePath, labelX, labelY] = useMemo(
    () =>
      getBezierPath({
        sourceX,
        sourceY,
        targetX,
        targetY,
        sourcePosition,
        targetPosition,
        curvature: 0.35,
      }),
    [sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition],
  )

  const stroke = selected || hovered ? ACTIVE_STROKE : BASE_STROKE
  const strokeWidth = selected ? 3.6 : 3

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke,
          strokeWidth,
          strokeLinecap: 'round',
        }}
        interactionWidth={28}
      />
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={28}
        style={{ cursor: 'pointer' }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={(event) => {
          event.stopPropagation()
          edgeData?.onSelect?.(id)
        }}
      />
      <EdgeLabelRenderer>
        <button
          type="button"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            edgeData?.onDelete?.(id)
          }}
          className={`absolute flex items-center justify-center w-6 h-6 -translate-x-1/2 -translate-y-1/2 rounded-full border border-red-200 bg-red-50 text-red-500 shadow-sm transition-all hover:scale-110 hover:bg-red-100 hover:border-red-300 ${showDelete
            ? 'pointer-events-auto opacity-100'
            : 'pointer-events-none opacity-0'
            }`}
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
          aria-label="Delete edge"
          title="Delete edge"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </EdgeLabelRenderer>
    </>
  )
}
