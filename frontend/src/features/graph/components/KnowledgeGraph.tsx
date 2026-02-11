import { useTranslation } from 'react-i18next'
import ForceGraph2D from 'react-force-graph-2d'
import { Search, ZoomIn, ZoomOut, Maximize, Filter } from 'lucide-react'
import type { GraphData } from '../api/graph'
import { ModeSwitch, GraphMode } from './ModeSwitch'
import { GraphFilters } from './GraphFilters'
import { GraphTooltip } from './GraphTooltip'
import { useGraphData } from '../hooks/useGraphData'
import { useGraphInteraction } from '../hooks/useGraphInteraction'
import { useState } from 'react'

interface KnowledgeGraphProps {
  data: GraphData
  width?: number
  height?: number
  filterDateRange?: { start: string; end: string }
  onFilterDateRangeChange?: (range: { start: string; end: string }) => void
  mode?: GraphMode
  onModeChange?: (mode: GraphMode) => void
}

export function KnowledgeGraph({
  data,
  width = 800,
  height = 600,
  filterDateRange = { start: '', end: '' },
  onFilterDateRangeChange,
  mode,
  onModeChange,
}: KnowledgeGraphProps) {
  const { t } = useTranslation()
  const [showFilters, setShowFilters] = useState(false)

  const {
    filteredData,
    neighbors,
    linkStyles,
    availableTypes,
    colors,
    selectedTypes,
    setSelectedTypes,
  } = useGraphData({ data })

  const {
    fgRef,
    query,
    setQuery,
    hoverNode,
    selectedNode,
    selectedLink,
    tooltip,
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
    linkCanvasObject,
  } = useGraphInteraction({ data, filteredData, neighbors, colors, linkStyles })

  return (
    <div className="relative w-full h-full overflow-hidden flex group">
      {/* Controls Overlay */}
      <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
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

        <GraphFilters
          showFilters={showFilters}
          filterDateRange={filterDateRange}
          onFilterDateRangeChange={onFilterDateRangeChange}
          availableTypes={availableTypes}
          selectedTypes={selectedTypes}
          onSelectedTypesChange={setSelectedTypes}
        />
      </div>

      {/* View Switcher - Top Right */}
      {mode && onModeChange && (
        <div className="absolute top-4 right-4 z-10 bg-white/90 dark:bg-zinc-900/90 backdrop-blur rounded-lg shadow-sm">
          <ModeSwitch mode={mode} onModeChange={onModeChange} />
        </div>
      )}

      {/* Zoom Controls - Bottom Left */}
      <div className="absolute bottom-20 left-4 z-10 flex flex-col gap-2 bg-white/90 dark:bg-zinc-900/90 backdrop-blur p-2 rounded-md shadow-sm border border-zinc-200 dark:border-zinc-800">
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
      <GraphTooltip
        tooltip={tooltip}
        colors={colors}
        onClose={clearSelection}
        selectedNode={selectedNode}
        selectedLink={selectedLink}
      />

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
        linkColor={(link: any) => {
          if (highlightLinks.has(link.id)) return colors.primary;
          return hoverNode ? colors.border : colors.muted;
        }}
        linkLineDash={(link: any) => linkStyles.get(link.label) || []}
        linkWidth={(link: any) => highlightLinks.has(link.id) ? 2 : 1}
        linkDirectionalParticles={(link: any) => highlightLinks.has(link.id) ? 2 : 0}
        linkDirectionalParticleWidth={2}
        onNodeClick={handleNodeClick}
        onLinkClick={handleLinkClick}
        onNodeHover={(node) => {
          if (!selectedNode && !selectedLink) updateHighlight(node)
        }}
        onLinkHover={(link) => {
          if (!selectedNode && !selectedLink) handleLinkHover(link)
        }}
        onBackgroundClick={clearSelection}
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        backgroundColor={colors.background}
      />
    </div>
  )
}
