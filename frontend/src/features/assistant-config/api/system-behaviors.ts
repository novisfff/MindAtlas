import { apiClient } from '@/lib/api/client'
import type { AssistantWorkflow } from './workflows'

export type SystemBehaviorTargetType = 'workflow' | 'agent'

export interface SystemBehaviorContractField {
  name: string
  type: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array'
  required: boolean
  description: string
  itemsType?: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array' | null
}

export interface SystemBehaviorContractSummary {
  inputFields: SystemBehaviorContractField[]
  outputFields: SystemBehaviorContractField[]
}

export interface SystemBehaviorTargetSummary {
  id: string
  targetType: SystemBehaviorTargetType
  name: string
  description: string
  enabled: boolean
  isSystem: boolean
  isCanonicalDefault: boolean
  workflowId: string | null
  agentProfileId: string | null
  publishedVersionId: string | null
}

export interface SystemBehavior {
  behaviorKey: 'weekly_report_generation' | 'monthly_report_generation'
  name: string
  description: string
  supportedTargetTypes: SystemBehaviorTargetType[]
  currentBinding: SystemBehaviorTargetSummary
  canonicalDefaultTarget: SystemBehaviorTargetSummary
  fallbackPolicy: string
  contract: SystemBehaviorContractSummary
}

export interface CreateSystemBehaviorExampleWorkflowResponse {
  createdWorkflow: AssistantWorkflow
  systemBehavior: SystemBehavior
}

export interface CreateSystemBehaviorExampleWorkflowRequest {
  bindToBehavior?: boolean
}

export interface UpdateSystemBehaviorBindingRequest {
  targetType: SystemBehaviorTargetType
  workflowId?: string | null
  agentProfileId?: string | null
}

export const getSystemBehaviors = () =>
  apiClient.get<SystemBehavior[]>('/api/assistant-config/system-behaviors')

export const updateSystemBehaviorBinding = (
  behaviorKey: string,
  data: UpdateSystemBehaviorBindingRequest,
) =>
  apiClient.put<SystemBehavior>(`/api/assistant-config/system-behaviors/${behaviorKey}`, {
    body: data,
  })

export const resetSystemBehaviorBinding = (behaviorKey: string) =>
  apiClient.post<SystemBehavior>(`/api/assistant-config/system-behaviors/${behaviorKey}/reset`)

export const createSystemBehaviorExampleWorkflow = (
  behaviorKey: string,
  data: CreateSystemBehaviorExampleWorkflowRequest = {},
) =>
  apiClient.post<CreateSystemBehaviorExampleWorkflowResponse>(
    `/api/assistant-config/system-behaviors/${behaviorKey}/create-example-workflow`,
    { body: data },
  )
