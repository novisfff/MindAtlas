import type { InputParam, OutputParam } from '../../api/tools'

export interface WorkflowToolDefinition {
  name: string
  description?: string | null
  inputParams: InputParam[]
  outputParams: OutputParam[]
}
