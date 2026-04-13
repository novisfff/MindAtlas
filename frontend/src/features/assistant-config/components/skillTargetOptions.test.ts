import { describe, expect, it } from 'vitest'

import type { SystemBehaviorContractSummary } from '../api/system-behaviors'
import type { AssistantAgentProfile } from '../api/agents'
import type { AssistantWorkflow, CallableWorkflow } from '../api/workflows'
import { buildSystemBehaviorBindingTargets } from './skillTargetOptions'

const REPORT_CONTRACT: SystemBehaviorContractSummary = {
  inputFields: [
    { name: 'periodType', type: 'string', required: true, description: '' },
    { name: 'periodStart', type: 'string', required: true, description: '' },
    { name: 'periodEnd', type: 'string', required: true, description: '' },
    { name: 'entryCount', type: 'integer', required: true, description: '' },
  ],
  outputFields: [
    { name: 'summary', type: 'string', required: true, description: '' },
    { name: 'suggestions', type: 'array', itemsType: 'string', required: true, description: '' },
    { name: 'trends', type: 'string', required: true, description: '' },
  ],
}

function makeWorkflow(overrides: Partial<AssistantWorkflow> = {}): AssistantWorkflow {
  return {
    id: 'workflow-1',
    name: 'Weekly Report Workflow',
    description: 'workflow',
    isSystem: false,
    enabled: true,
    workflowVersion: 1,
    workflowViewport: null,
    nodes: [],
    edges: [],
    draftVersionId: 'draft-1',
    publishedVersionId: 'published-1',
    referencedSkillIds: [],
    referenceCount: 0,
    referencedSystemBehaviorKeys: [],
    systemBehaviorReferenceCount: 0,
    openclawReferenceCount: 0,
    createdAt: '2026-04-13T00:00:00Z',
    updatedAt: '2026-04-13T00:00:00Z',
    ...overrides,
  }
}

function makeCallableWorkflow(overrides: Partial<CallableWorkflow> = {}): CallableWorkflow {
  return {
    id: 'workflow-1',
    name: 'Weekly Report Workflow',
    description: 'workflow',
    publishedVersionId: 'published-1',
    inputMode: 'structured',
    outputMode: 'structured',
    inputParams: [
      { name: 'periodType', paramType: 'string', required: true },
      { name: 'periodStart', paramType: 'string', required: true },
      { name: 'periodEnd', paramType: 'string', required: true },
      { name: 'entryCount', paramType: 'integer', required: true },
    ],
    outputParams: [
      { name: 'summary', paramType: 'string', required: true },
      { name: 'suggestions', paramType: 'array', required: true, itemsType: 'string' },
      { name: 'trends', paramType: 'string', required: true },
    ],
    availableVersions: [],
    ...overrides,
  }
}

function makeAgent(overrides: Partial<AssistantAgentProfile> = {}): AssistantAgentProfile {
  return {
    id: 'agent-1',
    name: 'General Chat Agent',
    description: 'agent',
    systemPrompt: 'Be helpful.',
    tools: [],
    kbConfig: { enabled: false },
    modelSource: 'default',
    modelId: null,
    isSystem: false,
    enabled: true,
    draftVersionId: 'draft-agent-1',
    publishedVersionId: 'published-agent-1',
    referencedSkillIds: [],
    referenceCount: 0,
    referencedSystemBehaviorKeys: [],
    systemBehaviorReferenceCount: 0,
    openclawReferenceCount: 0,
    createdAt: '2026-04-13T00:00:00Z',
    updatedAt: '2026-04-13T00:00:00Z',
    ...overrides,
  }
}

describe('buildSystemBehaviorBindingTargets', () => {
  it('uses the published callable contract instead of draft workflow nodes', () => {
    const targets = buildSystemBehaviorBindingTargets(
      [makeWorkflow()],
      [],
      {
        callableWorkflows: [makeCallableWorkflow({ inputMode: 'text' })],
        systemBehaviorContract: REPORT_CONTRACT,
      },
    )

    expect(targets[0]?.bindable).toBe(false)
    expect(targets[0]?.disabledReason).toBe('unstructured_workflow')
  })

  it('marks published contract mismatches as unavailable for binding', () => {
    const targets = buildSystemBehaviorBindingTargets(
      [makeWorkflow()],
      [],
      {
        callableWorkflows: [makeCallableWorkflow({ outputParams: [{ name: 'summary', paramType: 'string', required: true }] })],
        systemBehaviorContract: REPORT_CONTRACT,
      },
    )

    expect(targets[0]?.bindable).toBe(false)
    expect(targets[0]?.disabledReason).toBe('contract_mismatch')
  })

  it('keeps matching published workflows bindable', () => {
    const targets = buildSystemBehaviorBindingTargets(
      [makeWorkflow()],
      [],
      {
        callableWorkflows: [makeCallableWorkflow()],
        systemBehaviorContract: REPORT_CONTRACT,
      },
    )

    expect(targets[0]?.bindable).toBe(true)
    expect(targets[0]?.disabledReason).toBeUndefined()
  })

  it('keeps agents unavailable until explicit system behavior contracts exist', () => {
    const targets = buildSystemBehaviorBindingTargets(
      [],
      [makeAgent()],
      {
        systemBehaviorContract: REPORT_CONTRACT,
      },
    )

    expect(targets[0]?.type).toBe('agent')
    expect(targets[0]?.bindable).toBe(false)
    expect(targets[0]?.disabledReason).toBe('agent_contract_unsupported')
  })
})
