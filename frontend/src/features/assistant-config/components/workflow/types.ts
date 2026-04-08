import type { InputParam, OutputParam } from '../../api/tools'
import type { CallableWorkflow, CallableWorkflowVersion, WorkflowContractParam } from '../../api/workflows'

export interface WorkflowToolDefinition {
  name: string
  displayName?: string
  description?: string | null
  inputParams: InputParam[]
  outputParams: OutputParam[]
}

export type WorkflowContractParamDefinition = WorkflowContractParam
export type CallableWorkflowDefinition = CallableWorkflow
export type CallableWorkflowVersionDefinition = CallableWorkflowVersion
