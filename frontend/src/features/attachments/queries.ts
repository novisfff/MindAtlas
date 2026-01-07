import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getEntryAttachments,
  uploadAttachment,
  deleteAttachment,
} from './api/attachments'

export const attachmentKeys = {
  byEntry: (entryId: string) => ['attachments', 'entry', entryId] as const,
}

export function useEntryAttachmentsQuery(entryId: string) {
  return useQuery({
    queryKey: attachmentKeys.byEntry(entryId),
    queryFn: () => getEntryAttachments(entryId),
    enabled: !!entryId,
  })
}

export function useUploadAttachmentMutation(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadAttachment(entryId, file),
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
