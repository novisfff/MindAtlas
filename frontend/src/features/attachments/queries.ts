import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getEntryAttachments,
  uploadAttachment,
  deleteAttachment,
  retryAttachmentParse,
  retryAttachmentIndex,
} from './api/attachments'

export const attachmentKeys = {
  byEntry: (entryId: string) => ['attachments', 'entry', entryId] as const,
}

export function useEntryAttachmentsQuery(entryId: string) {
  return useQuery({
    queryKey: attachmentKeys.byEntry(entryId),
    queryFn: () => getEntryAttachments(entryId),
    enabled: !!entryId,
    refetchInterval: (query) => {
      const data = query.state.data
      const hasActiveJobs = data?.some((a) => {
        const parsing = a.parseStatus === 'pending' || a.parseStatus === 'processing'
        const indexing =
          a.indexToKnowledgeGraph === true && (a.kgIndexStatus === 'pending' || a.kgIndexStatus === 'processing')
        return parsing || indexing
      })
      return hasActiveJobs ? 3000 : false
    },
  })
}

export function useUploadAttachmentMutation(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, indexToKg }: { file: File; indexToKg: boolean }) =>
      uploadAttachment(entryId, file, indexToKg),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: attachmentKeys.byEntry(entryId) })
    },
  })
}

export function useDeleteAttachmentMutation(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteAttachment,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: attachmentKeys.byEntry(entryId) })
    },
  })
}

export function useRetryAttachmentParseMutation(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: retryAttachmentParse,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: attachmentKeys.byEntry(entryId) })
    },
  })
}

export function useRetryAttachmentIndexMutation(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: retryAttachmentIndex,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: attachmentKeys.byEntry(entryId) })
    },
  })
}
