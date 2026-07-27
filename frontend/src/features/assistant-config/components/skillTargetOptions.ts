import type { AssistantAgentProfile } from '../api/agents'
import type { SystemBehaviorContractField, SystemBehaviorContractSummary } from '../api/system-behaviors'
import type { CallableWorkflow, WorkflowContractParam } from '../api/workflows'
import type { AssistantWorkflow } from '../api/workflows'
import { isStructuredStartWorkflowFromNodes } from './workflow/startNodeConfig'

/** Shared skill-target type leftovers after Plan 10 legacy skill admin removal. */
export type SkillTargetType = 'workflow' | 'agent'

const DEFAULT_SKILL_NAME = 'general_chat'
const DEFAULT_SYSTEM_TARGET_PREFIX = `${DEFAULT_SKILL_NAME}__`
export const SYSTEM_DEFAULT_TARGET_KEY = '__system_default_target__'

export interface AssistantExecutableTarget {
  key: string
  id: string
  type: SkillTargetType
  name: string
  description?: string
  folderId?: string | null
  enabled: boolean
  isSystem: boolean
  hidden?: boolean
  isSystemDefault?: boolean
  referenceCount: number
  systemBehaviorReferenceCount?: number
  openclawReferenceCount?: number
  referencedSystemBehaviorKeys?: string[]
  bindable: boolean
  disabledReason?: string
}

interface BuildTargetOptions {
  defaultTargetType?: SkillTargetType | null
  defaultTargetId?: string | null
  callableWorkflows?: CallableWorkflow[]
  systemBehaviorContract?: SystemBehaviorContractSummary
}

function matchesContractField(
  expected: SystemBehaviorContractField,
  actual: Pick<WorkflowContractParam, 'paramType' | 'required' | 'itemsType'> | undefined,
): boolean {
  if (!actual) return false
  if ((actual.paramType || '').trim().toLowerCase() !== expected.type) return false
  if (expected.required && !actual.required) return false
  if (expected.type === 'array') {
    return (actual.itemsType || '').trim().toLowerCase() === String(expected.itemsType || '').trim().toLowerCase()
  }
  return true
}

function matchesOutputField(
  expected: SystemBehaviorContractField,
  actual: Pick<WorkflowContractParam, 'paramType' | 'itemsType'> | undefined,
): boolean {
  if (!actual) return false
  if ((actual.paramType || '').trim().toLowerCase() !== expected.type) return false
  if (expected.type === 'array') {
    return (actual.itemsType || '').trim().toLowerCase() === String(expected.itemsType || '').trim().toLowerCase()
  }
  return true
}

function matchesSystemBehaviorContract(
  callableWorkflow: CallableWorkflow | undefined,
  contract: SystemBehaviorContractSummary | undefined,
): boolean {
  if (!contract) return true
  if (!callableWorkflow) return false
  if (callableWorkflow.inputMode !== 'structured' || callableWorkflow.outputMode !== 'structured') {
    return false
  }

  const inputParams = new Map(callableWorkflow.inputParams.map((param) => [param.name, param]))
  const outputParams = new Map(callableWorkflow.outputParams.map((param) => [param.name, param]))

  return (
    contract.inputFields.every((field) => matchesContractField(field, inputParams.get(field.name)))
    && contract.outputFields.every((field) => matchesOutputField(field, outputParams.get(field.name)))
  )
}

function inferSystemDefaultTarget(
  targets: AssistantExecutableTarget[],
  options?: BuildTargetOptions,
): AssistantExecutableTarget | undefined {
  const configured = targets.find((item) => (
    options?.defaultTargetId
    && options?.defaultTargetType
    && item.id === options.defaultTargetId
    && item.type === options.defaultTargetType
  ))
  if (configured) return configured

  return targets.find((item) => item.isSystem && item.name.startsWith(DEFAULT_SYSTEM_TARGET_PREFIX))
}

export function buildAssistantExecutableTargets(
  workflows: AssistantWorkflow[],
  agents: AssistantAgentProfile[],
  options?: BuildTargetOptions,
): AssistantExecutableTarget[] {
  const workflowTargets: AssistantExecutableTarget[] = workflows.map((workflow) => ({
    ...(
      isStructuredStartWorkflowFromNodes(
        (workflow.nodes ?? []).map((node) => ({
          nodeType: node.nodeType,
          config: node.config,
        })),
      )
        ? { bindable: false, disabledReason: 'structured_workflow' }
        : { bindable: true }
    ),
    key: `workflow:${workflow.id}`,
    id: workflow.id,
    type: 'workflow',
    name: workflow.name,
    description: workflow.description,
    folderId: workflow.folderId,
    enabled: workflow.enabled,
    isSystem: workflow.isSystem,
    hidden: workflow.hidden,
    referenceCount: workflow.referenceCount,
    systemBehaviorReferenceCount: workflow.systemBehaviorReferenceCount,
    openclawReferenceCount: workflow.openclawReferenceCount,
    referencedSystemBehaviorKeys: workflow.referencedSystemBehaviorKeys,
  }))

  const agentTargets: AssistantExecutableTarget[] = agents.map((agent) => ({
    key: `agent:${agent.id}`,
    id: agent.id,
    type: 'agent',
    name: agent.name,
    description: agent.description,
    folderId: agent.folderId,
    enabled: agent.enabled,
    isSystem: agent.isSystem,
    hidden: agent.hidden,
    referenceCount: agent.referenceCount,
    systemBehaviorReferenceCount: agent.systemBehaviorReferenceCount,
    openclawReferenceCount: agent.openclawReferenceCount,
    referencedSystemBehaviorKeys: agent.referencedSystemBehaviorKeys,
    bindable: true,
  }))

  const sortedTargets = [...workflowTargets, ...agentTargets].sort((a, b) => {
    if (a.isSystem !== b.isSystem) return a.isSystem ? -1 : 1
    return a.name.localeCompare(b.name)
  })

  const defaultTarget = inferSystemDefaultTarget(sortedTargets, options)
  if (defaultTarget) {
    return sortedTargets.map((item) => (
      item.key === defaultTarget.key
        ? { ...item, isSystemDefault: true }
        : item
    ))
  }
  return sortedTargets
}

export function buildSystemBehaviorBindingTargets(
  workflows: AssistantWorkflow[],
  agents: AssistantAgentProfile[],
  options?: BuildTargetOptions,
): AssistantExecutableTarget[] {
  const callableWorkflowById = new Map((options?.callableWorkflows ?? []).map((workflow) => [workflow.id, workflow]))
  const workflowTargets: AssistantExecutableTarget[] = workflows.map((workflow) => {
    const callableWorkflow = callableWorkflowById.get(workflow.id)
    const hasPublishedVersion = Boolean(workflow.publishedVersionId && callableWorkflow?.publishedVersionId)
    const isEnabled = Boolean(workflow.enabled)
    const isPublishedStructured = callableWorkflow?.inputMode === 'structured'
    const satisfiesContract = matchesSystemBehaviorContract(
      callableWorkflow,
      options?.systemBehaviorContract,
    )
    let disabledReason: string | undefined
    if (!hasPublishedVersion) disabledReason = 'unpublished_target'
    else if (!isPublishedStructured) disabledReason = 'unstructured_workflow'
    else if (!satisfiesContract) disabledReason = 'contract_mismatch'
    else if (!isEnabled) disabledReason = 'unavailable_target'
    return {
      key: `workflow:${workflow.id}`,
      id: workflow.id,
      type: 'workflow',
      name: workflow.name,
      description: workflow.description,
      folderId: workflow.folderId,
      enabled: workflow.enabled,
      isSystem: workflow.isSystem,
      hidden: workflow.hidden,
      isSystemDefault: false,
      referenceCount: workflow.referenceCount,
      systemBehaviorReferenceCount: workflow.systemBehaviorReferenceCount,
      openclawReferenceCount: workflow.openclawReferenceCount,
      referencedSystemBehaviorKeys: workflow.referencedSystemBehaviorKeys,
      bindable: hasPublishedVersion && isPublishedStructured && satisfiesContract && isEnabled,
      disabledReason,
    }
  })

  const agentTargets: AssistantExecutableTarget[] = agents.map((agent) => {
    const hasPublishedVersion = Boolean(agent.publishedVersionId)
    const isEnabled = Boolean(agent.enabled)
    // Agents do not currently publish a machine-checkable system-behavior I/O contract.
    const supportsSystemBehaviorContract = false
    let disabledReason: string | undefined
    if (!hasPublishedVersion) disabledReason = 'unpublished_target'
    else if (!isEnabled) disabledReason = 'unavailable_target'
    else if (!supportsSystemBehaviorContract) disabledReason = 'agent_contract_unsupported'
    return {
      key: `agent:${agent.id}`,
      id: agent.id,
      type: 'agent',
      name: agent.name,
      description: agent.description,
      folderId: agent.folderId,
      enabled: agent.enabled,
      isSystem: agent.isSystem,
      hidden: agent.hidden,
      isSystemDefault: false,
      referenceCount: agent.referenceCount,
      systemBehaviorReferenceCount: agent.systemBehaviorReferenceCount,
      openclawReferenceCount: agent.openclawReferenceCount,
      referencedSystemBehaviorKeys: agent.referencedSystemBehaviorKeys,
      bindable: hasPublishedVersion && supportsSystemBehaviorContract && isEnabled,
      disabledReason,
    }
  })

  const sortedTargets = [...workflowTargets, ...agentTargets].sort((a, b) => {
    if (a.isSystem !== b.isSystem) return a.isSystem ? -1 : 1
    return a.name.localeCompare(b.name)
  })

  const defaultTarget = inferSystemDefaultTarget(sortedTargets, options)
  const withDefaultMarked = defaultTarget
    ? sortedTargets.map((item) => (
        item.key === defaultTarget.key
          ? { ...item, isSystemDefault: true }
          : item
      ))
    : sortedTargets

  if (!defaultTarget) return withDefaultMarked

  const specialDefaultTarget: AssistantExecutableTarget = {
    ...defaultTarget,
    key: SYSTEM_DEFAULT_TARGET_KEY,
    isSystemDefault: true,
  }

  return [specialDefaultTarget, ...withDefaultMarked.filter((item) => item.key !== defaultTarget.key)]
}


export function resolveSkillTargetKey(
  skill: { targetType?: SkillTargetType | null; workflowId?: string | null; agentProfileId?: string | null } | undefined,
  availableTargets: AssistantExecutableTarget[] = [],
): string | null {
  if (!skill?.targetType) return null
  const defaultAlias = availableTargets.find((item) => item.key === SYSTEM_DEFAULT_TARGET_KEY)
  if (skill.targetType === 'workflow' && skill.workflowId) {
    if (defaultAlias && defaultAlias.type === 'workflow' && defaultAlias.id === skill.workflowId) {
      return SYSTEM_DEFAULT_TARGET_KEY
    }
    return `workflow:${skill.workflowId}`
  }
  if (skill.targetType === 'agent' && skill.agentProfileId) {
    if (defaultAlias && defaultAlias.type === 'agent' && defaultAlias.id === skill.agentProfileId) {
      return SYSTEM_DEFAULT_TARGET_KEY
    }
    return `agent:${skill.agentProfileId}`
  }
  return null
}
