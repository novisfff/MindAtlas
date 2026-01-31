import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getDashboardStats } from './api/stats'
import { getHeatmap, getDayEntries, type HeatmapParams } from './api/heatmap'
import { getLatestWeeklyReport, listWeeklyReports, generateWeeklyReport, getLatestMonthlyReport, listMonthlyReports, generateMonthlyReport } from './api/reports'
import { getWeeklyMetrics } from './api/weeklyMetrics'
import { getHotness } from './api/hotness'

export const dashboardKeys = {
  all: ['dashboard'] as const,
  stats: () => [...dashboardKeys.all, 'stats'] as const,
  heatmap: (params?: HeatmapParams) => [...dashboardKeys.all, 'heatmap', params] as const,
  dayEntries: (date: string, typeId?: string) => [...dashboardKeys.all, 'day-entries', date, typeId] as const,
  weeklyReport: () => [...dashboardKeys.all, 'weekly-report'] as const,
  weeklyReportList: (page: number) => [...dashboardKeys.all, 'weekly-report-list', page] as const,
  monthlyReport: () => [...dashboardKeys.all, 'monthly-report'] as const,
  monthlyReportList: (page: number) => [...dashboardKeys.all, 'monthly-report-list', page] as const,
  weeklyMetrics: () => [...dashboardKeys.all, 'weekly-metrics'] as const,
  hotness: () => [...dashboardKeys.all, 'hotness'] as const,
}

export function useDashboardStatsQuery() {
  return useQuery({
    queryKey: dashboardKeys.stats(),
    queryFn: getDashboardStats,
  })
}

export function useHeatmapQuery(params?: HeatmapParams) {
  return useQuery({
    queryKey: dashboardKeys.heatmap(params),
    queryFn: () => getHeatmap(params),
  })
}

export function useDayEntriesQuery(date: string, options?: { enabled?: boolean; typeId?: string }) {
  return useQuery({
    queryKey: dashboardKeys.dayEntries(date, options?.typeId),
    queryFn: () => getDayEntries(date, options?.typeId),
    enabled: options?.enabled ?? true,
  })
}

export function useLatestWeeklyReportQuery() {
  return useQuery({
    queryKey: dashboardKeys.weeklyReport(),
    queryFn: getLatestWeeklyReport,
  })
}

export function useWeeklyReportListQuery(page = 0, size = 10) {
  return useQuery({
    queryKey: dashboardKeys.weeklyReportList(page),
    queryFn: () => listWeeklyReports(page, size),
  })
}

export function useGenerateWeeklyReportMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: generateWeeklyReport,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.weeklyReport() })
      queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
    },
  })
}

export function useWeeklyMetricsQuery() {
  return useQuery({
    queryKey: dashboardKeys.weeklyMetrics(),
    queryFn: getWeeklyMetrics,
  })
}

export function useHotnessQuery() {
  return useQuery({
    queryKey: dashboardKeys.hotness(),
    queryFn: getHotness,
  })
}

export function useLatestMonthlyReportQuery() {
  return useQuery({
    queryKey: dashboardKeys.monthlyReport(),
    queryFn: getLatestMonthlyReport,
  })
}

export function useMonthlyReportListQuery(page = 0, size = 10) {
  return useQuery({
    queryKey: dashboardKeys.monthlyReportList(page),
    queryFn: () => listMonthlyReports(page, size),
  })
}

export function useGenerateMonthlyReportMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: generateMonthlyReport,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: dashboardKeys.monthlyReport() })
      queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
    },
  })
}
