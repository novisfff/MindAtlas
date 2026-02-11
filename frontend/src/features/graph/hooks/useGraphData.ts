import { useState, useEffect, useMemo } from 'react'
import type { GraphData, GraphNode, GraphLink } from '../api/graph'

// Helper to get CSS variable value
export const getThemeColor = (variable: string) => {
  if (typeof window === 'undefined') return '#000'
  const style = getComputedStyle(document.documentElement)
  const hsl = style.getPropertyValue(variable).split(' ').join(',')
  return `hsl(${hsl})`
}

// Bright Color Palette for LightRAG nodes - vibrant and modern
export const BRIGHT_PALETTE = [
  '#60A5FA', // Blue 400
  '#F97316', // Orange 500
  '#F472B6', // Pink 400
  '#34D399', // Emerald 400
  '#A78BFA', // Violet 400
  '#FBBF24', // Amber 400
  '#2DD4BF', // Teal 400
  '#FB7185', // Rose 400
  '#818CF8', // Indigo 400
  '#4ADE80', // Green 400
]

// Deterministic color generator for LightRAG nodes
export const getLightRagColor = (type: string) => {
  if (!type) return BRIGHT_PALETTE[0]
  let hash = 0
  for (let i = 0; i < type.length; i++) {
    hash = type.charCodeAt(i) + ((hash << 5) - hash)
  }
  return BRIGHT_PALETTE[Math.abs(hash) % BRIGHT_PALETTE.length]
}

export interface GraphColors {
  primary: string
  background: string
  text: string
  muted: string
  border: string
}

interface UseGraphDataProps {
  data: GraphData
}

export function useGraphData({ data }: UseGraphDataProps) {
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set())
  const [neighbors, setNeighbors] = useState<Map<string, Set<string>>>(new Map())
  const [colors, setColors] = useState<GraphColors>({
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

  // Available Types for Filter UI
  const availableTypes = useMemo(() => {
    return Array.from(new Set(data.nodes.map(n => n.typeName))).sort()
  }, [data])

  return {
    filteredData,
    neighbors,
    linkStyles,
    availableTypes,
    colors,
    selectedTypes,
    setSelectedTypes
  }
}
