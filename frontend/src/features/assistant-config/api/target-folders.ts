import { apiClient } from '@/lib/api/client'

export interface AssistantTargetFolderPathItem {
  id: string
  name: string
}

export interface AssistantTargetFolder {
  id: string
  name: string
  description: string
  parentId: string | null
  colorToken: string
  iconKey: string
  path: AssistantTargetFolderPathItem[]
  folderCount: number
  workflowCount: number
  agentCount: number
  directFolderCount: number
  directWorkflowCount: number
  directAgentCount: number
  lastActivityAt: string
  createdAt: string
  updatedAt: string
}

export interface CreateTargetFolderRequest {
  name: string
  description?: string
  parentId?: string | null
  colorToken?: string
  iconKey?: string
}

export interface UpdateTargetFolderRequest {
  name?: string
  description?: string
  parentId?: string | null
  colorToken?: string
  iconKey?: string
}

export interface MoveTargetRequest {
  targetType: 'workflow' | 'agent'
  targetId: string
  folderId: string | null
}

export interface MoveFolderRequest {
  folderId: string
  parentId: string | null
}

export const getTargetFolders = () =>
  apiClient.get<AssistantTargetFolder[]>('/api/assistant-config/target-folders')

export const createTargetFolder = (data: CreateTargetFolderRequest) =>
  apiClient.post<AssistantTargetFolder>('/api/assistant-config/target-folders', { body: data })

export const updateTargetFolder = (id: string, data: UpdateTargetFolderRequest) =>
  apiClient.put<AssistantTargetFolder>(`/api/assistant-config/target-folders/${id}`, { body: data })

export const deleteTargetFolder = (id: string) =>
  apiClient.delete(`/api/assistant-config/target-folders/${id}`)

export const moveTargetToFolder = (data: MoveTargetRequest) =>
  apiClient.post('/api/assistant-config/target-folders/move-target', { body: data })

export const moveFolder = (data: MoveFolderRequest) =>
  apiClient.post('/api/assistant-config/target-folders/move-folder', { body: data })
