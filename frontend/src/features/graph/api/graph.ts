import { apiClient } from '@/lib/api/client'

export interface GraphNode {
  id: string
  label: string
  typeId: string
  typeName: string
  color?: string
}

export interface GraphLink {
  id: string
  source: string
  target: string
  label: string
  color?: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export async function getGraphData(): Promise<GraphData> {
  return apiClient.get<GraphData>('/api/graph')
}
