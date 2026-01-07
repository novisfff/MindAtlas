import { useQuery } from '@tanstack/react-query'
import { getDashboardStats } from './api/stats'

export function useDashboardStatsQuery() {
  return useQuery({
    queryKey: ['dashboard', 'stats'],
    queryFn: getDashboardStats,
  })
}
