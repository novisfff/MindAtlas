import { useRef, useState, useCallback, useEffect } from 'react'
import { ForceGraphMethods } from 'react-force-graph-2d'
import type { GraphData, GraphNode, GraphLink } from '../api/graph'
import type { GraphColors } from './useGraphData'
import { getLightRagColor } from './useGraphData'
import type { TooltipState } from '../components/GraphTooltip'

interface UseGraphInteractionProps {
  data: GraphData
  filteredData: { nodes: GraphNode[], links: GraphLink[] }
  neighbors: Map<string, Set<string>>
  colors: GraphColors
  linkStyles: Map<string, number[]>
}

export function useGraphInteraction({
  data,
  filteredData,
  neighbors,
  colors,
  linkStyles
}: UseGraphInteractionProps) {
  const fgRef = useRef<ForceGraphMethods>()
  const [query, setQuery] = useState('')
  const [hoverNode, setHoverNode] = useState<any | null>(null)
  const [selectedNode, setSelectedNode] = useState<any | null>(null)
  const [selectedLink, setSelectedLink] = useState<any | null>(null)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const [highlightNodes, setHighlightNodes] = useState<Set<string>>(new Set())
  const [highlightLinks, setHighlightLinks] = useState<Set<string>>(new Set())

  const clearSelection = useCallback(() => {
    setSelectedNode(null)
    setSelectedLink(null)
    setHoverNode(null)
    setTooltip(null)
    setHighlightNodes(new Set())
    setHighlightLinks(new Set())
  }, [])

  // Adjust physics forces for more compact layout
  useEffect(() => {
    if (fgRef.current) {
      // Reduce repulsion to make nodes closer (default is usually around -30)
      // A smaller absolute number (closer to 0) means less repulsion
      fgRef.current.d3Force('charge')?.strength(-5)

      // Reduce link distance (default is 30)
      fgRef.current.d3Force('link')?.distance(25)

      if (fgRef.current.d3ReheatSimulation) {
        fgRef.current.d3ReheatSimulation()
      }
    }
  }, [filteredData])

  const updateHighlight = (node: any | null) => {
    setHoverNode(node)

    // Manage Tooltip
    if (node) {
      // Find screen coordinates
      const graphCoords = fgRef.current?.graph2ScreenCoords(node.x, node.y)
      if (graphCoords) {
        setTooltip({
          x: graphCoords.x,
          y: graphCoords.y - 20, // Offset above
          data: node,
          type: 'node'
        })
      }
    } else {
      setTooltip(null)
    }

    const newHighlightNodes = new Set<string>()
    const newHighlightLinks = new Set<string>()

    if (node) {
      newHighlightNodes.add(node.id)
      const nodeNeighbors = neighbors.get(node.id)
      if (nodeNeighbors) {
        nodeNeighbors.forEach(neighborId => newHighlightNodes.add(neighborId))
      }
      filteredData.links.forEach((link: any) => {
        const sourceId = typeof link.source === 'object' ? link.source.id : link.source
        const targetId = typeof link.target === 'object' ? link.target.id : link.target
        if (sourceId === node.id || targetId === node.id) {
          newHighlightLinks.add(link.id)
        }
      })
    }
    setHighlightNodes(newHighlightNodes)
    setHighlightLinks(newHighlightLinks)
  }

  const updateLinkHighlight = (link: any | null) => {
    if (!link) {
      setTooltip(null)
      setHighlightLinks(new Set())
      setHighlightNodes(new Set())
      return
    }

    // Calculate position same as hover
    const source = typeof link.source === 'object' ? link.source : data.nodes.find(n => n.id === link.source)
    const target = typeof link.target === 'object' ? link.target : data.nodes.find(n => n.id === link.target)

    if (source && target && fgRef.current) {
      const midX = (source.x + target.x) / 2
      const midY = (source.y + target.y) / 2
      const coords = fgRef.current.graph2ScreenCoords(midX, midY)

      setTooltip({
        x: coords.x,
        y: coords.y,
        data: link,
        type: 'link'
      })
    }

    setHighlightLinks(new Set([link.id]))
    setHighlightNodes(new Set([typeof link.source === 'object' ? link.source.id : link.source, typeof link.target === 'object' ? link.target.id : link.target]))
  }

  const handleSearch = () => {
    if (!query.trim()) return
    const targetNode = filteredData.nodes.find(n =>
      n.label.toLowerCase().includes(query.toLowerCase())
    )
    if (targetNode && fgRef.current) {
      // Zoom to node
      // Need to cast targetNode to any because d3 adds x/y
      const node = targetNode as any
      if (typeof node.x === 'number' && typeof node.y === 'number') {
        fgRef.current.centerAt(node.x, node.y, 1000)
        fgRef.current.zoom(4, 2000)
        setHoverNode(targetNode)
        updateHighlight(targetNode)
      }
    }
  }

  const handleLinkClick = useCallback((link: any) => {
    setSelectedLink(link)
    setSelectedNode(null)
    updateLinkHighlight(link)
  }, [neighbors, data])

  const handleLinkHover = (link: any | null) => {
    if (selectedLink) return // Don't override selection
    updateLinkHighlight(link)
  }

  const handleNodeClick = useCallback((node: any) => {
    setSelectedLink(null)
    setSelectedNode(node)
    updateHighlight(node)
  }, [neighbors]) // Added dependency to match logic use

  const handleZoomIn = () => {
    if (fgRef.current) {
      const currentZoom = fgRef.current.zoom()
      fgRef.current.zoom(currentZoom * 1.5, 400)
    }
  }

  const handleZoomOut = () => {
    if (fgRef.current) {
      const currentZoom = fgRef.current.zoom()
      fgRef.current.zoom(currentZoom / 1.5, 400)
    }
  }

  const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const isHovered = node === hoverNode
    const isNeighbor = hoverNode && neighbors.get(hoverNode.id)?.has(node.id)
    const isDimmed = hoverNode && !isHovered && !isNeighbor

    // Config
    const label = node.label || ''
    const fontSize = isHovered ? 14 / globalScale : 12 / globalScale
    const nodeR = isHovered ? 8 : 6

    // Opacity
    ctx.globalAlpha = isDimmed ? 0.2 : 1

    // Draw Glow for hovered
    if (isHovered) {
      ctx.beginPath()
      ctx.arc(node.x, node.y, nodeR + 4, 0, 2 * Math.PI)
      ctx.fillStyle = colors.primary
      ctx.globalAlpha = 0.3
      ctx.fill()
      ctx.globalAlpha = 1
    }

    // Draw Node
    ctx.beginPath()
    ctx.arc(node.x, node.y, nodeR, 0, 2 * Math.PI)
    // System graph: use original color from backend; LightRAG: use bright palette
    const isLightRagNode = node.typeId?.startsWith('lightrag:')
    ctx.fillStyle = isLightRagNode
      ? getLightRagColor(node.entityType || node.typeName)
      : (node.color || colors.primary)
    ctx.fill()

    // Draw Border
    ctx.strokeStyle = colors.background
    ctx.lineWidth = 1.5 / globalScale
    ctx.stroke()

    // Simplified Label rendering for performance - show only if hovered or significant zoom
    if (isHovered || globalScale > 1.5) {
      ctx.font = `${isHovered ? 'bold ' : ''}${fontSize}px Sans-Serif`
      const textWidth = ctx.measureText(label).width
      const bckgDimensions = [textWidth, fontSize].map(n => n + fontSize * 0.5)

      // Label background
      ctx.fillStyle = isHovered ? colors.primary : 'rgba(255, 255, 255, 0.9)'
      if (isHovered) {
        if (ctx.roundRect) {
          ctx.roundRect(
            node.x - bckgDimensions[0] / 2,
            node.y + nodeR + 2,
            bckgDimensions[0],
            bckgDimensions[1],
            4
          )
        } else {
          ctx.rect(
            node.x - bckgDimensions[0] / 2,
            node.y + nodeR + 2,
            bckgDimensions[0],
            bckgDimensions[1]
          )
        }
        ctx.fill()
      } else {
        // Subtle background for regular labels
        ctx.fillStyle = 'rgba(255, 255, 255, 0.6)'
        ctx.fillRect(
          node.x - bckgDimensions[0] / 2,
          node.y + nodeR + 2,
          bckgDimensions[0],
          bckgDimensions[1]
        )
      }

      // Text
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = isHovered ? '#fff' : colors.text
      ctx.fillText(label, node.x, node.y + nodeR + 2 + bckgDimensions[1] / 2)
    }
  }, [hoverNode, neighbors, colors])

  const linkCanvasObject = useCallback((link: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const source = link.source
    const target = link.target
    if (!source || !target || typeof source.x !== 'number' || typeof target.x !== 'number') return

    const isHighlighted = highlightLinks.has(link.id)
    const isDimmed = hoverNode && !highlightLinks.has(link.id)

    // Calculate midpoint
    const midX = (source.x + target.x) / 2
    const midY = (source.y + target.y) / 2

    // Draw the line first
    ctx.beginPath()
    ctx.moveTo(source.x, source.y)
    ctx.lineTo(target.x, target.y)
    ctx.strokeStyle = isHighlighted ? colors.primary : (isDimmed ? colors.border : colors.muted)
    ctx.lineWidth = isHighlighted ? 2 / globalScale : 1 / globalScale
    const dashPattern = linkStyles.get(link.label) || []
    ctx.setLineDash(dashPattern.map(d => d / globalScale))
    ctx.globalAlpha = isDimmed ? 0.2 : 1
    ctx.stroke()
    ctx.setLineDash([])
    ctx.globalAlpha = 1

    // Draw label only when zoomed in enough or highlighted
    const label = link.label || ''
    if (label && (globalScale > 0.8 || isHighlighted)) {
      const fontSize = Math.max(8, 10 / globalScale)
      ctx.font = `${fontSize}px Sans-Serif`
      const textWidth = ctx.measureText(label).width
      const padding = 2 / globalScale

      // Background for readability
      ctx.fillStyle = 'rgba(255, 255, 255, 0.85)'
      ctx.fillRect(
        midX - textWidth / 2 - padding,
        midY - fontSize / 2 - padding,
        textWidth + padding * 2,
        fontSize + padding * 2
      )

      // Text
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = isHighlighted ? colors.primary : '#666'
      ctx.globalAlpha = isDimmed ? 0.3 : 1
      ctx.fillText(label, midX, midY)
      ctx.globalAlpha = 1
    }
  }, [hoverNode, highlightLinks, colors, linkStyles])

  return {
    fgRef,
    query,
    setQuery,
    hoverNode,
    selectedNode,
    selectedLink,
    tooltip,
    highlightNodes,
    highlightLinks,
    handleSearch,
    updateHighlight,
    handleLinkClick,
    handleLinkHover,
    handleNodeClick,
    clearSelection,
    handleZoomIn,
    handleZoomOut,
    nodeCanvasObject,
    linkCanvasObject
  }
}
