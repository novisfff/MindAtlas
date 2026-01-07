import { useQuery } from '@tanstack/react-query'
import { getGraphData } from './api/graph'

export const graphKeys = {
  data: ['graph'] as const,
}

export function useGraphDataQuery() {
  return useQuery({
    queryKey: graphKeys.data,
    queryFn: getGraphData,
  })
}
