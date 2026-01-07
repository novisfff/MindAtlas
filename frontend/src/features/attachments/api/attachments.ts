import { apiClient } from '@/lib/api/client'
import type { Attachment } from '@/types'

export async function getEntryAttachments(entryId: string): Promise<Attachment[]> {
  return apiClient.get<Attachment[]>(`/api/attachments/entry/${encodeURIComponent(entryId)}`)
}

export async function uploadAttachment(entryId: string, file: File): Promise<Attachment> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`/api/attachments/entry/${encodeURIComponent(entryId)}`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw new Error('Upload failed')
  }

  const result = await response.json()
  return result.data
}

export async function deleteAttachment(id: string): Promise<void> {
  await apiClient.delete<void>(`/api/attachments/${encodeURIComponent(id)}`)
}

export function getDownloadUrl(id: string): string {
  return `/api/attachments/${encodeURIComponent(id)}/download`
}
