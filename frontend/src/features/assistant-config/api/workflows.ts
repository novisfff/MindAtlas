import { apiClient } from '@/lib/api/client'
import type {
  WorkflowEdge,
  WorkflowHumanApproval,
  WorkflowCopilotRequest,
  WorkflowCopilotResponse,
  WorkflowInput,
  WorkflowNode,
  WorkflowTestRunRequest,
  WorkflowTestStreamOptions,
  WorkflowValidationResponse,
} from './workflow'
import { runWorkflowTestStream } from './workflow'

export interface AssistantWorkflow {
  id: string
  name: string
  description: string
  isSystem: boolean
  enabled: boolean
  workflowVersion: number
  workflowViewport: { x: number; y: number; zoom: number } | null
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  draftVersionId: string | null
  publishedVersionId: string | null
  referencedSkillIds: string[]
  referenceCount: number
  referencedSystemBehaviorKeys: string[]
  systemBehaviorReferenceCount: number
  openclawReferenceCount: number
  createdAt: string
  updatedAt: string
}

export interface CreateWorkflowRequest {
  name: string
  description?: string
  enabled?: boolean
  workflow?: WorkflowInput
}

export interface UpdateWorkflowRequest {
  name?: string
  description?: string
  enabled?: boolean
  workflow?: WorkflowInput
}

export interface WorkflowVersionRecord {
  id: string
  sequenceNo: number
  versionName: string
  versionSource: 'save' | 'publish'
  createdAt: string
  updatedAt: string
}

export interface WorkflowVersionListPayload {
  workflowId: string
  draftVersionId: string | null
  publishedVersionId: string | null
  versions: WorkflowVersionRecord[]
}

export interface WorkflowPublishRequest {
  workflow: WorkflowInput
  versionName?: string | null
  description?: string
}

export interface WorkflowApprovalDecisionRequest {
  decision: 'approved' | 'rejected'
  values: Record<string, unknown>
  comment?: string
}

export interface WorkflowRollbackResponse {
  draftVersionId: string | null
  publishedVersionId: string | null
  workflow: WorkflowInput | null
}

export interface WorkflowDeleteVersionResponse {
  deletedVersionId: string
  draftVersionId: string | null
  publishedVersionId: string | null
}

export interface WorkflowClearVersionsResponse {
  deletedCount: number
  keptLatestVersionId: string | null
  draftVersionId: string | null
  publishedVersionId: string | null
}

export interface WorkflowContractParam {
  name: string
  description?: string | null
  paramType: string
  required: boolean
  nullable?: boolean
  itemsType?: string | null
  enum?: string[] | null
}

export type WorkflowContractInputMode = 'text' | 'structured'
export type WorkflowContractOutputMode = 'text' | 'structured'

export interface CallableWorkflowVersion {
  id: string
  sequenceNo: number
  versionName: string
  versionSource: 'publish' | 'save'
  inputMode: WorkflowContractInputMode
  outputMode: WorkflowContractOutputMode
  inputParams: WorkflowContractParam[]
  outputParams: WorkflowContractParam[]
  createdAt: string
  updatedAt: string
}

export interface CallableWorkflow {
  id: string
  name: string
  description: string | null
  publishedVersionId: string
  inputMode: WorkflowContractInputMode
  outputMode: WorkflowContractOutputMode
  inputParams: WorkflowContractParam[]
  outputParams: WorkflowContractParam[]
  availableVersions: CallableWorkflowVersion[]
}

export const getWorkflows = () =>
  apiClient.get<AssistantWorkflow[]>('/api/assistant-config/workflows')

export const getCallableWorkflows = () =>
  apiClient.get<CallableWorkflow[]>('/api/assistant-config/workflows/callable')

export const getWorkflow = (id: string) =>
  apiClient.get<AssistantWorkflow>(`/api/assistant-config/workflows/${id}`)

export const createWorkflow = (data: CreateWorkflowRequest) =>
  apiClient.post<AssistantWorkflow>('/api/assistant-config/workflows', { body: data })

export const updateWorkflowEntity = (id: string, data: UpdateWorkflowRequest) =>
  apiClient.put<AssistantWorkflow>(`/api/assistant-config/workflows/${id}`, { body: data })

export const copyWorkflow = (id: string) =>
  apiClient.post<AssistantWorkflow>(`/api/assistant-config/workflows/${id}/copy`)

export const listWorkflowVersions = (id: string) =>
  apiClient.get<WorkflowVersionListPayload>(`/api/assistant-config/workflows/${id}/versions`)

export const publishWorkflow = (id: string, data: WorkflowPublishRequest) =>
  apiClient.post<AssistantWorkflow>(`/api/assistant-config/workflows/${id}/publish`, { body: data })

export const rollbackWorkflowVersion = (id: string, versionId: string) =>
  apiClient.post<WorkflowRollbackResponse>(`/api/assistant-config/workflows/${id}/versions/${versionId}/rollback`)

export const deleteWorkflowVersion = (id: string, versionId: string) =>
  apiClient.delete<WorkflowDeleteVersionResponse>(`/api/assistant-config/workflows/${id}/versions/${versionId}`)

export const clearWorkflowVersions = (id: string) =>
  apiClient.post<WorkflowClearVersionsResponse>(`/api/assistant-config/workflows/${id}/versions/clear`)

export const deleteWorkflow = (
  id: string,
  options: { confirmRebindSystemBehaviors?: boolean } = {},
) =>
  apiClient.delete(`/api/assistant-config/workflows/${id}`, {
    query: {
      confirmRebindSystemBehaviors: options.confirmRebindSystemBehaviors ?? false,
    },
  })

export const saveWorkflowById = (
  workflowId: string,
  data: {
    workflow: WorkflowInput
    description?: string
  },
) =>
  apiClient.put(`/api/assistant-config/workflows/${workflowId}`, { body: data })

export const validateWorkflowById = (workflowId: string, data: WorkflowInput) =>
  apiClient.post<WorkflowValidationResponse>(
    `/api/assistant-config/workflows/${workflowId}/validate`,
    { body: data },
  )

export const respondWorkflowCopilotById = (
  workflowId: string,
  payload: WorkflowCopilotRequest,
) =>
  apiClient.post<WorkflowCopilotResponse>(
    `/api/assistant-config/workflows/${workflowId}/copilot/respond`,
    { body: payload },
  )

export const runWorkflowTestStreamById = (
  workflowId: string,
  payload: WorkflowTestRunRequest,
  options: WorkflowTestStreamOptions = {},
) => runWorkflowTestStream(
  workflowId,
  payload,
  {
    ...options,
    path: `/api/assistant-config/workflows/${workflowId}/test-run`,
  },
)

export const submitWorkflowRunApprovalDecision = (
  runId: string,
  approvalId: string,
  payload: WorkflowApprovalDecisionRequest,
) =>
  apiClient.post<WorkflowHumanApproval>(
    `/api/assistant-config/runs/${runId}/approvals/${approvalId}/decision`,
    { body: payload },
  )
