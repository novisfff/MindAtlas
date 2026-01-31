import { apiClient } from '@/lib/api/client'

export interface WeeklyMetrics {
  weekEntryCount: number
  activeDays: number
  totalEntries: number
  totalRelations: number
  weekStart: string
  weekEnd: string
}

export async function getWeeklyMetrics(): Promise<WeeklyMetrics> {
  return apiClient.get<WeeklyMetrics>('/api/stats/weekly-metrics')
}
