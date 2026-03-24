import type { AssistantAgentProfile } from '../api/agents'
import type { AssistantSkill, SkillTargetType } from '../api/skills'
import type { AssistantWorkflow } from '../api/workflows'
import { isStructuredStartWorkflowFromNodes } from './workflow/startNodeConfig'

const DEFAULT_SKILL_NAME = 'general_chat'
const DEFAULT_SYSTEM_TARGET_PREFIX = `${DEFAULT_SKILL_NAME}__`
export const SYSTEM_DEFAULT_TARGET_KEY = '__system_default_target__'

export interface AssistantExecutableTarget {
  key: string
  id: string
  type: SkillTargetType
  name: string
  description?: string
  enabled: boolean
  isSystem: boolean
  isSystemDefault?: boolean
  referenceCount: number
  systemBehaviorReferenceCount?: number
  referencedSystemBehaviorKeys?: string[]
  bindable: boolean
  disabledReason?: string
}

interface BuildTargetOptions {
  defaultTargetType?: SkillTargetType | null
  defaultTargetId?: string | null
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
    enabled: workflow.enabled,
    isSystem: workflow.isSystem,
    referenceCount: workflow.referenceCount,
    systemBehaviorReferenceCount: workflow.systemBehaviorReferenceCount,
    referencedSystemBehaviorKeys: workflow.referencedSystemBehaviorKeys,
  }))

  const agentTargets: AssistantExecutableTarget[] = agents.map((agent) => ({
    key: `agent:${agent.id}`,
    id: agent.id,
    type: 'agent',
    name: agent.name,
    description: agent.description,
    enabled: agent.enabled,
    isSystem: agent.isSystem,
    referenceCount: agent.referenceCount,
    systemBehaviorReferenceCount: agent.systemBehaviorReferenceCount,
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
  const workflowTargets: AssistantExecutableTarget[] = workflows.map((workflow) => {
    const isStructured = isStructuredStartWorkflowFromNodes(
      (workflow.nodes ?? []).map((node) => ({
        nodeType: node.nodeType,
        config: node.config,
      })),
    )
    const hasPublishedVersion = Boolean(workflow.publishedVersionId)
    const isEnabled = Boolean(workflow.enabled)
    let disabledReason: string | undefined
    if (!isStructured) disabledReason = 'unstructured_workflow'
    if (!hasPublishedVersion) disabledReason = 'unpublished_target'
    if (!isEnabled) disabledReason = 'unavailable_target'
    return {
      key: `workflow:${workflow.id}`,
      id: workflow.id,
      type: 'workflow',
      name: workflow.name,
      description: workflow.description,
      enabled: workflow.enabled,
      isSystem: workflow.isSystem,
      isSystemDefault: false,
      referenceCount: workflow.referenceCount,
      systemBehaviorReferenceCount: workflow.systemBehaviorReferenceCount,
      referencedSystemBehaviorKeys: workflow.referencedSystemBehaviorKeys,
      bindable: isStructured && hasPublishedVersion && isEnabled,
      disabledReason,
    }
  })

  const agentTargets: AssistantExecutableTarget[] = agents.map((agent) => {
    const hasPublishedVersion = Boolean(agent.publishedVersionId)
    const isEnabled = Boolean(agent.enabled)
    let disabledReason: string | undefined
    if (!hasPublishedVersion) disabledReason = 'unpublished_target'
    if (!isEnabled) disabledReason = 'unavailable_target'
    return {
      key: `agent:${agent.id}`,
      id: agent.id,
      type: 'agent',
      name: agent.name,
      description: agent.description,
      enabled: agent.enabled,
      isSystem: agent.isSystem,
      isSystemDefault: false,
      referenceCount: agent.referenceCount,
      systemBehaviorReferenceCount: agent.systemBehaviorReferenceCount,
      referencedSystemBehaviorKeys: agent.referencedSystemBehaviorKeys,
      bindable: hasPublishedVersion && isEnabled,
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

export function buildSkillBindingTargets(
  workflows: AssistantWorkflow[],
  agents: AssistantAgentProfile[],
  options?: BuildTargetOptions,
): AssistantExecutableTarget[] {
  const realTargets = buildAssistantExecutableTargets(workflows, agents, options)
  const defaultTarget = realTargets.find((item) => item.isSystemDefault)
  if (!defaultTarget) return realTargets

  const specialDefaultTarget: AssistantExecutableTarget = {
    ...defaultTarget,
    key: SYSTEM_DEFAULT_TARGET_KEY,
    isSystemDefault: true,
  }

  return [specialDefaultTarget, ...realTargets.filter((item) => item.key !== defaultTarget.key)]
}

export function resolveSkillTargetKey(
  skill: Pick<AssistantSkill, 'targetType' | 'workflowId' | 'agentProfileId'> | undefined,
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
