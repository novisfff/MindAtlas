import { apiClient } from '@/lib/api/client'

export interface WeeklyReportContent {
  summary: string | null
  suggestions: string[]
  trends: string | null
}

export interface WeeklyReport {
  id: string
  weekStart: string
  weekEnd: string
  entryCount: number
  content: WeeklyReportContent | null
  status: string
  attempts: number
  lastError: string | null
  generatedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface WeeklyReportListResponse {
  items: WeeklyReport[]
  total: number
  page: number
  size: number
}

export async function getLatestWeeklyReport(): Promise<WeeklyReport | null> {
  return apiClient.get<WeeklyReport | null>('/api/reports/weekly/latest')
}

export async function listWeeklyReports(page = 0, size = 10): Promise<WeeklyReportListResponse> {
  return apiClient.get<WeeklyReportListResponse>(`/api/reports/weekly?page=${page}&size=${size}`)
}

export async function generateWeeklyReport(): Promise<WeeklyReport> {
  return apiClient.post<WeeklyReport>('/api/reports/weekly/generate')
}

// Monthly Report Types
export interface MonthlyReportContent {
  summary: string | null
  suggestions: string[]
  trends: string | null
}

export interface MonthlyReport {
  id: string
  monthStart: string
  monthEnd: string
  entryCount: number
  content: MonthlyReportContent | null
  status: string
  attempts: number
  lastError: string | null
  generatedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface MonthlyReportListResponse {
  items: MonthlyReport[]
  total: number
  page: number
  size: number
}

// Monthly Report API
export async function getLatestMonthlyReport(): Promise<MonthlyReport | null> {
  return apiClient.get<MonthlyReport | null>('/api/reports/monthly/latest')
}

export async function listMonthlyReports(page = 0, size = 10): Promise<MonthlyReportListResponse> {
  return apiClient.get<MonthlyReportListResponse>(`/api/reports/monthly?page=${page}&size=${size}`)
}

export async function generateMonthlyReport(): Promise<MonthlyReport> {
  return apiClient.post<MonthlyReport>('/api/reports/monthly/generate')
}
