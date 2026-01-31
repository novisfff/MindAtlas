import { apiClient } from '@/lib/api/client'

export interface TypeHotness {
  typeId: string
  typeName: string
  typeColor: string | null
  count: number
}

export interface TagHotness {
  tagId: string
  tagName: string
  tagColor: string | null
  count: number
}

export interface HotnessResponse {
  topTypes: TypeHotness[]
  topTags: TagHotness[]
  windowStart: string
  windowEnd: string
}

export async function getHotness(): Promise<HotnessResponse> {
  return apiClient.get<HotnessResponse>('/api/stats/hotness')
}
