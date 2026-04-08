import type { Node } from '@xyflow/react'
import type { ContainerBodyNode, ContainerBodyNodeType, NodeConfig, NodeType } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import type {
  CallableWorkflowDefinition,
  CallableWorkflowVersionDefinition,
  WorkflowToolDefinition,
} from './types'
import { createDefaultIfElseConfig } from './ifElseConfig'
import { defaultLabelForNodeType } from './labelUtils'
import { getDefaultCodeTemplate } from './property-panel/nodes/codeExecutorTemplates'

function buildToolInputBindings(toolDef?: WorkflowToolDefinition): Record<string, string> {
  if (!toolDef) return {}
  return Object.fromEntries((toolDef.inputParams ?? []).map((item) => [item.name, '']))
}

export function resolveCallableWorkflowVersion(
  workflowDef?: CallableWorkflowDefinition,
  versionId?: string | null,
): CallableWorkflowVersionDefinition | undefined {
  if (!workflowDef) return undefined
  const versions = workflowDef.availableVersions ?? []
  if (versionId) {
    const matched = versions.find((item) => item.id === versionId)
    if (matched) return matched
  }
  return versions.find((item) => item.id === workflowDef.publishedVersionId) ?? versions[0]
}

function buildWorkflowCallInputBindings(versionDef?: CallableWorkflowVersionDefinition): Record<string, string> {
  if (!versionDef) return {}
  return Object.fromEntries((versionDef.inputParams ?? []).map((item) => [item.name, '']))
}

export function createDefaultNodeConfig(
  nodeType: NodeType | ContainerBodyNodeType,
  options?: {
    toolName?: string
    toolDef?: WorkflowToolDefinition
    workflowDef?: CallableWorkflowDefinition
    workflowVersionId?: string | null
  },
): NodeConfig | null {
  if (nodeType === 'start') {
    return {
      inputMode: 'text',
      memoryMode: 'auto',
      structuredFields: [],
      sessionVars: [],
    }
  }

  if (nodeType === 'llm') {
    return {
      outputMode: 'text',
      userInput: '{{start.user_input}}',
      modelSource: 'default',
    }
  }

  if (nodeType === 'agent') {
    return {
      userInput: '{{start.user_input}}',
      toolNames: [],
      maxIterations: 12,
      knowledgeEnabled: false,
      modelSource: 'default',
    }
  }

  if (nodeType === 'output') {
    return {
      outputMode: 'text',
      textTemplate: '{{start.user_input}}',
    }
  }

  if (nodeType === 'tool') {
    return {
      toolName: options?.toolName ?? '',
      inputBindings: buildToolInputBindings(options?.toolDef),
    }
  }

  if (nodeType === 'workflow_call') {
    const workflowVersion = resolveCallableWorkflowVersion(options?.workflowDef, options?.workflowVersionId)
    return {
      targetWorkflowId: options?.workflowDef?.id ?? '',
      bindingMode: 'pinned',
      targetPublishedVersionId: workflowVersion?.id ?? options?.workflowDef?.publishedVersionId ?? null,
      inputBindings: buildWorkflowCallInputBindings(workflowVersion),
    }
  }

  if (nodeType === 'if_else') {
    return createDefaultIfElseConfig()
  }

  if (nodeType === 'parameter_extractor') {
    return {
      modelSource: 'default',
      inputContent: '',
      outputFields: [{ name: 'result', type: 'string', nullable: false }],
    }
  }

  if (nodeType === 'knowledge_retrieval') {
    return { query: '{{start.user_input}}' }
  }

  if (nodeType === 'code_executor') {
    return {
      language: 'python',
      entrypoint: 'main',
      inputBindings: {
        arg1: '',
        arg2: '',
      },
      outputFields: [
        { name: 'result', type: 'string', nullable: false },
      ],
      code: getDefaultCodeTemplate('python'),
    }
  }

  if (nodeType === 'http_request') {
    return {
      method: 'GET',
      url: '',
      headers: [],
      queryParams: [],
      bodyType: 'none',
      jsonBodyTemplate: '',
      rawBodyTemplate: '',
      formBody: [],
      authType: 'none',
      bearerToken: '',
      apiKeyIn: 'header',
      apiKeyName: 'X-API-Key',
      apiKeyValue: '',
      timeoutMs: 15000,
      retryEnabled: false,
      maxRetries: 2,
      retryIntervalMs: 200,
      verifySsl: true,
    }
  }

  if (nodeType === 'variable_assign') {
    return {
      variableName: '',
      operation: 'set',
      valueTemplate: '',
    }
  }

  if (nodeType === 'human_in_loop') {
    return {
      title: '',
      instruction: '',
      fields: [
        {
          name: 'value',
          label: 'Value',
          type: 'string',
          required: true,
          valueTemplate: '',
        },
      ],
      approveLabel: '',
      rejectLabel: '',
      requireRejectComment: true,
    }
  }

  if (nodeType === 'iteration') {
    return {
      inputSource: '',
      outputVariable: 'results',
      outputSelector: '{{container.item}}',
      parallelMode: false,
      errorStrategy: 'fail_fast',
      flattenOutput: true,
      bodyNodes: [
        {
          nodeId: 'start',
          nodeType: 'start',
          label: defaultLabelForNodeType('start'),
          positionX: 40,
          positionY: 72,
          config: null,
        },
      ],
      bodyEdges: [],
    }
  }

  if (nodeType === 'loop') {
    return {
      initialVars: [],
      updateMappings: [],
      terminationLogic: 'and',
      terminationConditions: [],
      maxIterations: 10,
      bodyNodes: [
        {
          nodeId: 'start',
          nodeType: 'start',
          label: defaultLabelForNodeType('start'),
          positionX: 40,
          positionY: 72,
          config: null,
        },
      ],
      bodyEdges: [],
    }
  }

  return null
}

export function createMainFlowNode(params: {
  id: string
  nodeType: NodeType
  position: { x: number; y: number }
  label?: string
  toolName?: string
  toolDef?: WorkflowToolDefinition
  workflowDef?: CallableWorkflowDefinition
  workflowVersionId?: string | null
}): Node<WfNodeData> {
  const { id, nodeType, position, label, toolName, toolDef, workflowDef, workflowVersionId } = params
  return {
    id,
    type: nodeType,
    position,
    data: {
      nodeType,
      label: label || defaultLabelForNodeType(nodeType),
      config: createDefaultNodeConfig(nodeType, { toolName, toolDef, workflowDef, workflowVersionId }),
    },
  }
}

export function createSubflowNode(params: {
  nodeId: string
  nodeType: ContainerBodyNodeType
  positionX: number
  positionY: number
  label?: string
  toolName?: string
  toolDef?: WorkflowToolDefinition
  workflowDef?: CallableWorkflowDefinition
  workflowVersionId?: string | null
}): ContainerBodyNode {
  const { nodeId, nodeType, positionX, positionY, label, toolName, toolDef, workflowDef, workflowVersionId } = params
  const config = createDefaultNodeConfig(nodeType, { toolName, toolDef, workflowDef, workflowVersionId })
  return {
    nodeId,
    nodeType,
    label: label || defaultLabelForNodeType(nodeType),
    positionX,
    positionY,
    config: config && typeof config === 'object' ? (config as Record<string, unknown>) : null,
  }
}
