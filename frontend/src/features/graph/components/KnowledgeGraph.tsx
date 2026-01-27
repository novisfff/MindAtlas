import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import ForceGraph2D, { ForceGraphMethods } from 'react-force-graph-2d'
import { Search, ZoomIn, ZoomOut, Maximize, Filter, Calendar, Layers, Clock } from 'lucide-react'
import type { GraphData, GraphNode, GraphLink } from '../api/graph'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'

interface KnowledgeGraphProps {
  data: GraphData
  width?: number
  height?: number
  filterDateRange?: { start: string; end: string }
  onFilterDateRangeChange?: (range: { start: string; end: string }) => void
}

// Helper to get CSS variable value
const getThemeColor = (variable: string) => {
  if (typeof window === 'undefined') return '#000'
  const style = getComputedStyle(document.documentElement)
  const hsl = style.getPropertyValue(variable).split(' ').join(',')
  return `hsl(${hsl})`
}

export function KnowledgeGraph({
  data,
  width = 800,
  height = 600,
  filterDateRange = { start: '', end: '' },
  onFilterDateRangeChange,
}: KnowledgeGraphProps) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()

  const dateLocale = i18n.language.startsWith('zh') ? zhCN : enUS
  const dateFormatStr = i18n.language.startsWith('zh') ? 'yyyy年M月d日' : 'MMM d, yyyy'
  const fgRef = useRef<ForceGraphMethods>()
  const [query, setQuery] = useState('')
  const [hoverNode, setHoverNode] = useState<GraphNode | null>(null)

  // Tooltip State
  const [tooltip, setTooltip] = useState<{ x: number, y: number, content: React.ReactNode } | null>(null)

  // Filter States
  const [showFilters, setShowFilters] = useState(false)
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set())

  // Neighbors map for fast lookup: node.id -> Set<neighborId>
  const [neighbors, setNeighbors] = useState<Map<string, Set<string>>>(new Map())
  const [highlightNodes, setHighlightNodes] = useState<Set<string>>(new Set())
  const [highlightLinks, setHighlightLinks] = useState<Set<string>>(new Set())
  const [colors, setColors] = useState({
    primary: '#3b82f6',
    background: '#ffffff',
    text: '#1f2937',
    muted: '#9ca3af',
    border: '#e5e7eb'
  })

  // Update theme colors on mount
  useEffect(() => {
    setColors({
      primary: getThemeColor('--primary'),
      background: getThemeColor('--background'),
      text: getThemeColor('--foreground'),
      muted: getThemeColor('--muted-foreground'),
      border: getThemeColor('--border'),
    })
  }, [])

  // Initialize selected types with all available types
  useEffect(() => {
    const allTypes = new Set(data.nodes.map(n => n.typeName))
    setSelectedTypes(allTypes)
  }, [data])

  // Computed Filtered Data (only type filter, time filter is handled by backend)
  const filteredData = useMemo(() => {
    const filteredNodes = data.nodes.filter(node => {
      // Type Filter
      if (!selectedTypes.has(node.typeName)) return false
      return true
    })

    const nodeIds = new Set(filteredNodes.map(n => n.id))
    const filteredLinks = data.links.filter((link: any) => {
      const sourceId = typeof link.source === 'object' ? link.source.id : link.source
      const targetId = typeof link.target === 'object' ? link.target.id : link.target
      return nodeIds.has(sourceId) && nodeIds.has(targetId)
    })

    return { nodes: filteredNodes, links: filteredLinks }
  }, [data, selectedTypes])

  // Update neighbors based on FILTERED data
  useEffect(() => {
    const neighborMap = new Map<string, Set<string>>()
    filteredData.links.forEach((link: any) => {
      const sourceId = typeof link.source === 'object' ? link.source.id : link.source
      const targetId = typeof link.target === 'object' ? link.target.id : link.target

      if (!neighborMap.has(sourceId)) neighborMap.set(sourceId, new Set())
      if (!neighborMap.has(targetId)) neighborMap.set(targetId, new Set())

      neighborMap.get(sourceId)?.add(targetId)
      neighborMap.get(targetId)?.add(sourceId)
    })
    setNeighbors(neighborMap)
  }, [filteredData])

  // Link Styling Map
  const linkStyles = useMemo(() => {
    // Generate unique dash styles for each relationship type
    const styles = new Map<string, number[]>()
    const uniqueLabels = Array.from(new Set(data.links.map(l => l.label))).sort()

    // Pattern: [Solid, Dashed, Dotted, Dash-Dot, etc.]
    const patterns = [
      [],             // Solid
      [4, 2],         // Dashed
      [2, 2],         // Dotted
      [6, 3, 2, 3],   // Dash-Dot
      [8, 4],         // Long Dash
      [2, 4],         // Sparse Dot
    ]

    uniqueLabels.forEach((label, index) => {
      styles.set(label, patterns[index % patterns.length])
    })
    return styles
  }, [data])

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

  // Handle Search
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

  const updateHighlight = (node: any | null) => {
    setHoverNode(node)

    // Manage Tooltip
    if (node) {
      // Format time display
      let timeText = ''
      if (node.timeMode === 'POINT' && node.timeAt) {
        timeText = format(new Date(node.timeAt), dateFormatStr, { locale: dateLocale })
      } else if (node.timeMode === 'RANGE') {
        const from = node.timeFrom ? format(new Date(node.timeFrom), dateFormatStr, { locale: dateLocale }) : t('labels.unknown')
        const to = node.timeTo ? format(new Date(node.timeTo), dateFormatStr, { locale: dateLocale }) : t('time.present')
        timeText = `${from} → ${to}`
      }

      // Find screen coordinates
      const graphCoords = fgRef.current?.graph2ScreenCoords(node.x, node.y)
      if (graphCoords) {
        setTooltip({
          x: graphCoords.x,
          y: graphCoords.y - 20, // Offset above
          content: (
            <div className="bg-white/95 dark:bg-zinc-900/95 backdrop-blur rounded-lg shadow-xl border border-zinc-200 dark:border-zinc-800 max-w-[240px] overflow-hidden">
              <div className="h-1 w-full" style={{ backgroundColor: node.color || colors.muted }}></div>
              <div className="p-3 pt-2">
                <div className="font-semibold text-zinc-900 dark:text-zinc-100 text-sm mb-1">{node.label}</div>
                <div className="text-xs text-zinc-500 mb-2">{node.typeName}</div>
                {node.summary ? (
                  <div className="text-xs text-zinc-600 dark:text-zinc-400 line-clamp-3 leading-relaxed">
                    {node.summary}
                  </div>
                ) : (
                  <div className="text-xs italic text-zinc-400">{t('pages.graph.noSummary')}</div>
                )}
                {timeText && (
                  <div className="flex items-center text-[10px] text-zinc-500 mt-2 gap-1">
                    <Clock className="w-3 h-3" />
                    <span>{timeText}</span>
                  </div>
                )}
                {node.createdAt && (
                  <div className={`text-[10px] text-zinc-400 ${timeText ? 'mt-1' : 'mt-2 pt-2 border-t border-zinc-100 dark:border-zinc-800'}`}>
                    {t('labels.created')}: {format(new Date(node.createdAt), 'yyyy-MM-dd')}
                  </div>
                )}
              </div>
            </div>
          )
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

  const handleLinkHover = (link: any | null) => {
    if (link) {
      // Calculate midpoint roughly for tooltip (simplified)
      // Or just use pointer event? ForceGraph2D doesn't pass event easily here
      // We'll use graph center of link if possible, or just skip position and fix later
      // Actually, better to just show "Tooltip" at mouse position if we had access to it.
      // Since we don't have mouse event here easily without custom handler, 
      // we can approximate with link source/target average
      const source = typeof link.source === 'object' ? link.source : data.nodes.find(n => n.id === link.source)
      const target = typeof link.target === 'object' ? link.target : data.nodes.find(n => n.id === link.target)

      if (source && target && fgRef.current) {
        const midX = (source.x + target.x) / 2
        const midY = (source.y + target.y) / 2
        const coords = fgRef.current.graph2ScreenCoords(midX, midY)

        setTooltip({
          x: coords.x,
          y: coords.y,
          content: (
            <div className="p-2 bg-white/95 dark:bg-zinc-900/95 backdrop-blur rounded shadow-lg border border-zinc-200 dark:border-zinc-800 text-xs">
              <div className="font-medium text-zinc-900 dark:text-zinc-100 mb-1 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                {link.label}
              </div>
              <div className="text-zinc-500 flex items-center gap-1">
                <span className="truncate max-w-[80px]">{source.label}</span>
                <span>→</span>
                <span className="truncate max-w-[80px]">{target.label}</span>
              </div>
            </div>
          )
        })
      }
      setHighlightLinks(new Set([link.id]))
      setHighlightNodes(new Set([typeof link.source === 'object' ? link.source.id : link.source, typeof link.target === 'object' ? link.target.id : link.target]))
    } else {
      if (!hoverNode) {
        setTooltip(null)
        setHighlightLinks(new Set())
        setHighlightNodes(new Set())
      }
    }
  }

  const handleNodeClick = useCallback((node: any) => {
    navigate(`/entries/${node.id}`)
  }, [navigate])

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
    ctx.fillStyle = node.color || colors.primary
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

  // Available Types for Filter UI
  const availableTypes = useMemo(() => {
    return Array.from(new Set(data.nodes.map(n => n.typeName))).sort()
  }, [data])

  return (
    <div className="relative w-full h-full border rounded-lg overflow-hidden bg-white dark:bg-zinc-950 shadow-sm flex group">
      {/* Controls Overlay */}
      <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
        {/* Search & Filter Trigger */}
        <div className="flex gap-2">
          <div className="flex items-center gap-2 bg-white/90 dark:bg-zinc-900/90 backdrop-blur p-1 rounded-md shadow-sm border border-zinc-200 dark:border-zinc-800">
            <Search className="w-4 h-4 text-zinc-500 ml-2" />
            <input
              className="bg-transparent border-none outline-none text-sm w-40 text-zinc-900 dark:text-zinc-100 placeholder:text-zinc-400"
              placeholder={t('pages.graph.searchPlaceholder')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            />
          </div>

          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`p-2 rounded-md shadow-sm border transition bg-white/90 dark:bg-zinc-900/90 backdrop-blur ${showFilters ? 'bg-zinc-100 dark:bg-zinc-800 border-zinc-400 dark:border-zinc-600' : 'border-zinc-200 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800'}`}
            title={t('pages.graph.filterLogic')}
          >
            <Filter className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
          </button>
        </div>

        {/* Filter Panel */}
        {showFilters && (
          <div className="bg-white/95 dark:bg-zinc-900/95 backdrop-blur p-4 rounded-md shadow-lg border border-zinc-200 dark:border-zinc-800 w-64 flex flex-col gap-4 animate-in fade-in slide-in-from-top-2">
            {/* Time Filter */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                <Calendar className="w-3 h-3" />
                <span>{t('pages.graph.filterTime')}</span>
              </div>
              <div className="flex flex-col gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-muted-foreground">{t('labels.from')}</span>
                  <input
                    type="date"
                    className="w-full text-sm bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1"
                    value={filterDateRange.start}
                    onChange={(e) => onFilterDateRangeChange?.({ ...filterDateRange, start: e.target.value })}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-muted-foreground">{t('labels.to')}</span>
                  <input
                    type="date"
                    className="w-full text-sm bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1"
                    value={filterDateRange.end}
                    onChange={(e) => onFilterDateRangeChange?.({ ...filterDateRange, end: e.target.value })}
                  />
                </label>
              </div>
            </div>

            <div className="h-px bg-zinc-100 dark:bg-zinc-800" />

            {/* Type Filter */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  <Layers className="w-3 h-3" />
                  <span>{t('pages.graph.filterType')}</span>
                </div>
                <button
                  className="text-[10px] text-blue-500 hover:underline"
                  onClick={() => setSelectedTypes(new Set(availableTypes))}
                >
                  {t('actions.reset')}
                </button>
              </div>
              <div className="max-h-40 overflow-y-auto flex flex-col gap-1 pr-1">
                {availableTypes.map(type => (
                  <label key={type} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50 p-1 rounded">
                    <input
                      type="checkbox"
                      checked={selectedTypes.has(type)}
                      onChange={(e) => {
                        const next = new Set(selectedTypes)
                        if (e.target.checked) next.add(type)
                        else next.delete(type)
                        setSelectedTypes(next)
                      }}
                      className="rounded border-zinc-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="truncate" title={type}>{type}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="absolute bottom-20 right-4 z-10 flex flex-col gap-2 bg-white/90 dark:bg-zinc-900/90 backdrop-blur p-2 rounded-md shadow-sm border border-zinc-200 dark:border-zinc-800">
        <button
          className="p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded transition"
          onClick={handleZoomIn}
          title={t('pages.graph.zoomIn')}
        >
          <ZoomIn className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
        </button>
        <button
          className="p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded transition"
          onClick={handleZoomOut}
          title={t('pages.graph.zoomOut')}
        >
          <ZoomOut className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
        </button>
        <button
          className="p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded transition"
          onClick={() => fgRef.current?.zoomToFit(400)}
          title={t('pages.graph.fitView')}
        >
          <Maximize className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
        </button>
      </div>

      {/* Tooltip Popup */}
      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none transform -translate-x-1/2 -translate-y-full mb-2 animate-in fade-in zoom-in-95 duration-150"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          {tooltip.content}
        </div>
      )}

      <ForceGraph2D
        ref={fgRef}
        graphData={filteredData}
        width={width}
        height={height}
        nodeId="id"
        nodeLabel="label"
        nodeCanvasObject={nodeCanvasObject}
        nodePointerAreaPaint={(node, color, ctx) => {
          ctx.beginPath()
          ctx.arc(node.x!, node.y!, 10, 0, 2 * Math.PI)
          ctx.fillStyle = color
          ctx.fill()
        }}
        // Link Styling
        linkColor={(link: any) => {
          if (highlightLinks.has(link.id)) return colors.primary;
          return hoverNode ? colors.border : colors.muted;
        }}
        linkLineDash={(link: any) => linkStyles.get(link.label) || []}
        linkWidth={(link: any) => highlightLinks.has(link.id) ? 2 : 1}
        linkDirectionalParticles={(link: any) => highlightLinks.has(link.id) ? 2 : 0}
        linkDirectionalParticleWidth={2}
        onNodeClick={handleNodeClick}
        onNodeHover={updateHighlight}
        onLinkHover={handleLinkHover}
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        backgroundColor={colors.background}
      />
    </div>
  )
}
