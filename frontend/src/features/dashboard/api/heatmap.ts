import { apiClient } from '@/lib/api/client'

export interface HeatmapEntry {
  id: string
  title: string
}

export interface HeatmapDay {
  date: string
  count: number
  pointCount: number
  rangeStartCount: number
  rangeActiveCount: number
  entries: HeatmapEntry[]
}

export interface HeatmapResponse {
  startDate: string
  endDate: string
  data: HeatmapDay[]
}

export interface HeatmapParams {
  months?: number
  typeId?: string
}

export async function getHeatmap(params?: HeatmapParams): Promise<HeatmapResponse> {
  const searchParams = new URLSearchParams()
  if (params?.months) searchParams.set('months', String(params.months))
  if (params?.typeId) searchParams.set('typeId', params.typeId)
  const query = searchParams.toString()
  return apiClient.get<HeatmapResponse>(`/api/stats/heatmap${query ? `?${query}` : ''}`)
}

export type CoverKind = 'POINT' | 'RANGE_START' | 'RANGE_SPAN'

export interface DayEntry {
  id: string
  title: string
  timeMode: 'POINT' | 'RANGE'
  timeAt: string | null
  timeFrom: string | null
  timeTo: string | null
  coverKind: CoverKind
  typeColor: string | null
}

export interface DayEntriesResponse {
  date: string
  entries: DayEntry[]
}

export async function getDayEntries(date: string, typeId?: string): Promise<DayEntriesResponse> {
  const searchParams = new URLSearchParams()
  searchParams.set('date', date)
  if (typeId) searchParams.set('typeId', typeId)
  return apiClient.get<DayEntriesResponse>(`/api/stats/day-entries?${searchParams.toString()}`)
}
