import { apiClient } from '@/lib/api/client'

export interface GraphNode {
  id: string
  label: string
  typeId: string
  typeName: string
  color?: string
  createdAt?: string
  summary?: string
  timeMode?: 'NONE' | 'POINT' | 'RANGE'
  timeAt?: string
  timeFrom?: string
  timeTo?: string
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

export interface GraphFilterParams {
  timeFrom?: string
  timeTo?: string
}

export async function getGraphData(params?: GraphFilterParams): Promise<GraphData> {
  const searchParams = new URLSearchParams()
  if (params?.timeFrom) searchParams.append('timeFrom', params.timeFrom)
  if (params?.timeTo) searchParams.append('timeTo', params.timeTo)
  const query = searchParams.toString()
  return apiClient.get<GraphData>(`/api/graph${query ? `?${query}` : ''}`)
}
