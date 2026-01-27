import { useQuery } from '@tanstack/react-query'
import { getGraphData, GraphFilterParams } from './api/graph'

export const graphKeys = {
  data: (params?: GraphFilterParams) => ['graph', params] as const,
}

export function useGraphDataQuery(params?: GraphFilterParams) {
  return useQuery({
    queryKey: graphKeys.data(params),
    queryFn: () => getGraphData(params),
  })
}
