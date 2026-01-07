import { useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphData } from '../api/graph'

interface KnowledgeGraphProps {
  data: GraphData
  width?: number
  height?: number
}

export function KnowledgeGraph({ data, width = 800, height = 600 }: KnowledgeGraphProps) {
  const navigate = useNavigate()
  const fgRef = useRef<any>()

  const handleNodeClick = useCallback((node: any) => {
    navigate(`/entries/${node.id}`)
  }, [navigate])

  const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = node.label || ''
    const fontSize = 12 / globalScale
    ctx.font = `${fontSize}px Sans-Serif`

    // Draw node circle
    ctx.beginPath()
    ctx.arc(node.x, node.y, 8, 0, 2 * Math.PI)
    ctx.fillStyle = node.color || '#6B7280'
    ctx.fill()

    // Draw label
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillStyle = '#374151'
    ctx.fillText(label, node.x, node.y + 16)
  }, [])

  return (
    <ForceGraph2D
      ref={fgRef}
      graphData={data}
      width={width}
      height={height}
      nodeId="id"
      nodeLabel="label"
      nodeCanvasObject={nodeCanvasObject}
      nodePointerAreaPaint={(node, color, ctx) => {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, 12, 0, 2 * Math.PI)
        ctx.fillStyle = color
        ctx.fill()
      }}
      linkSource="source"
      linkTarget="target"
      linkLabel="label"
      linkColor={(link: any) => link.color || '#9CA3AF'}
      linkWidth={2}
      linkDirectionalArrowLength={6}
      linkDirectionalArrowRelPos={1}
      onNodeClick={handleNodeClick}
      cooldownTicks={100}
      onEngineStop={() => fgRef.current?.zoomToFit(400)}
    />
  )
}
