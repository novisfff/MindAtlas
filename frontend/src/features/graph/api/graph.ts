import { apiClient } from '@/lib/api/client'

export interface GraphNode {
  id: string
  label: string
  typeId: string
  typeName: string
  color?: string
  createdAt?: string
  summary?: string
  // LightRAG-specific fields
  entityId?: string
  entityType?: string
  description?: string
  entryId?: string
  entryTitle?: string
  // System graph time fields
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
  // LightRAG-specific fields
  description?: string
  keywords?: string
  entryId?: string
  entryTitle?: string
  createdAt?: string
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

export interface LightRagGraphParams {
  nodeLabel?: string
  maxDepth?: number
  maxNodes?: number
}

export async function getLightRagGraphData(params?: LightRagGraphParams): Promise<GraphData> {
  const searchParams = new URLSearchParams()
  if (params?.nodeLabel) searchParams.append('nodeLabel', params.nodeLabel)
  if (params?.maxDepth) searchParams.append('maxDepth', String(params.maxDepth))
  if (params?.maxNodes) searchParams.append('maxNodes', String(params.maxNodes))
  const query = searchParams.toString()
  return apiClient.get<GraphData>(`/api/lightrag/graph${query ? `?${query}` : ''}`)
}
