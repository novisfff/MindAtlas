import { beforeEach, describe, expect, it } from 'vitest'
import { useWorkflowTestRunStore } from './workflow-test-run-store'
import type { WorkflowHumanApproval } from '../api/workflow'

function makeApproval(overrides: Partial<WorkflowHumanApproval> = {}): WorkflowHumanApproval {
  return {
    id: 'approval-1',
    runId: 'run-1',
    channelType: 'workflow_test',
    conversationId: null,
    messageId: null,
    workflowId: null,
    skillId: null,
    nodeId: 'human_confirm',
    nodeLabel: '人工确认',
    status: 'pending',
    requestPayload: {},
    fieldSchema: [],
    initialValues: {},
    submittedValues: {},
    decision: null,
    comment: null,
    resolvedAt: null,
    createdAt: '2026-04-16T00:00:00.000Z',
    updatedAt: '2026-04-16T00:00:00.000Z',
    ...overrides,
  }
}

function makeTurn(approval: WorkflowHumanApproval) {
  return {
    runId: approval.runId,
    status: 'completed' as const,
    startedAt: '2026-04-16T00:00:00.000Z',
    result: {
      finalText: '',
      finalJson: null,
      errorMessage: null,
      durationMs: null,
    },
    deltaSummary: {
      content: { chunks: 0, chars: 0 },
      nodes: {},
    },
    traceEvents: [],
    pendingApprovals: [approval],
    nodeSnapshots: {},
    nodeTraceMap: {},
    sessionMemory: {
      conversationSummary: '',
      skillFacts: [],
      workflowCallScopes: {},
    },
  }
}

describe('useWorkflowTestRunStore approval events', () => {
  beforeEach(() => {
    useWorkflowTestRunStore.getState().reset()
  })

  it('resolves selected run approvals even when there is no active run', () => {
    const pendingApproval = makeApproval()
    const resolvedApproval = makeApproval({
      status: 'approved',
      decision: 'approved',
      submittedValues: { title: 'done' },
      resolvedAt: '2026-04-16T00:01:00.000Z',
    })

    useWorkflowTestRunStore.setState({
      activeRunId: null,
      selectedRunId: pendingApproval.runId,
      pendingApprovals: [pendingApproval],
      turnsByRunId: {
        [pendingApproval.runId]: makeTurn(pendingApproval),
      },
    })

    useWorkflowTestRunStore.getState().ingestEvent({
      event: 'human_approval_resolved',
      data: {
        runId: pendingApproval.runId,
        approval: resolvedApproval,
        ts: '2026-04-16T00:01:00.000Z',
      },
    })

    const state = useWorkflowTestRunStore.getState()
    expect(state.pendingApprovals).toEqual([])
    expect(state.turnsByRunId[pendingApproval.runId]?.pendingApprovals).toEqual([])
  })
})
