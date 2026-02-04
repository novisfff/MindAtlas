import { apiClient } from '@/lib/api/client'
import type { Attachment, AttachmentMarkdownResponse } from '@/types'

export async function getEntryAttachments(entryId: string): Promise<Attachment[]> {
  return apiClient.get<Attachment[]>(`/api/attachments/entry/${encodeURIComponent(entryId)}`)
}

export async function uploadAttachment(
  entryId: string,
  file: File,
  indexToKnowledgeGraph: boolean = false
): Promise<Attachment> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('index_to_knowledge_graph', String(indexToKnowledgeGraph))

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

export async function retryAttachmentParse(id: string): Promise<Attachment> {
  return apiClient.post<Attachment>(`/api/attachments/${encodeURIComponent(id)}/retry`)
}

export async function retryAttachmentIndex(id: string): Promise<Attachment> {
  return apiClient.post<Attachment>(`/api/attachments/${encodeURIComponent(id)}/retry-index`)
}

export function getDownloadUrl(id: string): string {
  return `/api/attachments/${encodeURIComponent(id)}/download`
}

export function getPreviewUrl(id: string): string {
  return `/api/attachments/${encodeURIComponent(id)}/view`
}

export async function getAttachmentMarkdown(id: string): Promise<AttachmentMarkdownResponse> {
  return apiClient.get<AttachmentMarkdownResponse>(`/api/attachments/${encodeURIComponent(id)}/markdown`)
}
