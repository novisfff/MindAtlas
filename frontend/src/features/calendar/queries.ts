import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/lib/api/client'
import type { Entry, Page } from '@/types'

const DEFAULT_CALENDAR_PAGE_SIZE = 100

interface CalendarEntriesParams {
  timeFrom?: string
  timeTo?: string
}

export function useCalendarEntriesQuery(params: CalendarEntriesParams) {
  return useQuery({
    queryKey: ['calendar-entries', params],
    queryFn: async () => {
      const searchParams = new URLSearchParams()
      if (params.timeFrom) {
        searchParams.set('timeFrom', `${params.timeFrom}T00:00:00Z`)
      }
      if (params.timeTo) {
        searchParams.set('timeTo', `${params.timeTo}T23:59:59Z`)
      }
      searchParams.set('size', String(DEFAULT_CALENDAR_PAGE_SIZE))

      const response = await apiClient.get<Page<Entry>>(
        `/api/entries?${searchParams.toString()}`
      )
      return response.content.filter((e) => e.timeMode !== 'NONE')
    },
  })
}

interface PatchEntryTimeParams {
  id: string
  timeMode?: 'NONE' | 'POINT' | 'RANGE'
  timeAt?: string
  timeFrom?: string
  timeTo?: string
}

export function usePatchEntryTimeMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (params: PatchEntryTimeParams) => {
      const { id, ...body } = params
      return apiClient.patch<Entry>(`/api/entries/${id}/time`, { body })
    },
    onMutate: async (params) => {
      await queryClient.cancelQueries({ queryKey: ['calendar-entries'] })
      const previousData = queryClient.getQueriesData<Entry[]>({
        queryKey: ['calendar-entries'],
      })

      queryClient.setQueriesData<Entry[]>(
        { queryKey: ['calendar-entries'] },
        (old) => {
          if (!old) return old
          return old.map((entry) => {
            if (entry.id !== params.id) return entry
            return {
              ...entry,
              timeMode: params.timeMode ?? entry.timeMode,
              timeAt: params.timeAt ?? entry.timeAt,
              timeFrom: params.timeFrom ?? entry.timeFrom,
              timeTo: params.timeTo ?? entry.timeTo,
            }
          })
        }
      )

      return { previousData }
    },
    onError: (_err, _params, context) => {
      if (context?.previousData) {
        for (const [queryKey, data] of context.previousData) {
          queryClient.setQueryData(queryKey, data)
        }
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar-entries'] })
      queryClient.invalidateQueries({ queryKey: ['entries'] })
    },
  })
}
