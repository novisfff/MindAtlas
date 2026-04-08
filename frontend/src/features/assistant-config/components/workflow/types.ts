import type { InputParam, OutputParam } from '../../api/tools'

export interface WorkflowToolDefinition {
  name: string
  displayName?: string
  description?: string | null
  inputParams: InputParam[]
  outputParams: OutputParam[]
}
