import { apiClient } from '@/lib/api/client'

export interface TypeCount {
  typeId: string
  typeName: string
  typeColor: string | null
  count: number
}

export interface DashboardStats {
  totalEntries: number
  totalTags: number
  totalRelations: number
  entriesByType: TypeCount[]
}

export async function getDashboardStats(): Promise<DashboardStats> {
  return apiClient.get<DashboardStats>('/api/stats/dashboard')
}
