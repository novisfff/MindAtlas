import { useQuery } from '@tanstack/react-query'
import { getGraphData, getLightRagGraphData, GraphFilterParams, LightRagGraphParams } from './api/graph'

export const graphKeys = {
  data: (params?: GraphFilterParams) => ['graph', params] as const,
  lightrag: (params?: LightRagGraphParams) => ['graph', 'lightrag', params] as const,
}

export function useGraphDataQuery(params?: GraphFilterParams) {
  return useQuery({
    queryKey: graphKeys.data(params),
    queryFn: () => getGraphData(params),
  })
}

export function useLightRagGraphQuery(params?: LightRagGraphParams, enabled: boolean = true) {
  return useQuery({
    queryKey: graphKeys.lightrag(params),
    queryFn: () => getLightRagGraphData(params),
    enabled,
  })
}
