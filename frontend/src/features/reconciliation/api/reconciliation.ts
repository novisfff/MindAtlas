import { apiClient } from '@/lib/api/client'

export type ReconciliationDecision = 'mark_succeeded' | 'mark_failed' | 'mark_compensated'

export interface ReconciliationCall {
  callId: string
  runId: string
  status: 'unknown' | 'needs_reconciliation'
  stateRevision: number
  runRevision: number
  failureCode: string | null
  executionMode: string
  sideEffectStartedAt: string | null
  createdAt: string | null
  updatedAt: string | null
  attemptCount: number
  evidenceRequired: boolean
}

export interface ReconciliationPage {
  items: ReconciliationCall[]
  total: number
}

export interface ReconcileInput {
  expectedCallRevision: number
  expectedRunRevision: number
  decision: ReconciliationDecision
  evidenceArtifactIds: string[]
  requestId: string
  reason: string
}

export interface ReconcileResult {
  callId: string
  decision: ReconciliationDecision
  resultingCallStatus: string
  resultingCallRevision: number
  resultingRunRevision: number
  reconciliationId: string
  created: boolean
}

export class ReconciliationResponseError extends Error {
  constructor() {
    super('invalid_reconciliation_response')
    this.name = 'ReconciliationResponseError'
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SAFE = /^[a-z][a-z0-9_]{0,95}$/

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ReconciliationResponseError()
  return value as Record<string, unknown>
}

function id(value: unknown): string {
  if (typeof value !== 'string' || !UUID.test(value)) throw new ReconciliationResponseError()
  return value
}

function nonNegative(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw new ReconciliationResponseError()
  return value
}

function time(value: unknown): string | null {
  if (value == null) return null
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) throw new ReconciliationResponseError()
  return value
}

export function parseReconciliationPage(value: unknown): ReconciliationPage {
  const raw = object(value)
  if (!Array.isArray(raw.items)) throw new ReconciliationResponseError()
  return {
    items: raw.items.map((item) => {
      const row = object(item)
      const status = row.status
      if (status !== 'unknown' && status !== 'needs_reconciliation') throw new ReconciliationResponseError()
      return {
        callId: id(row.callId),
        runId: id(row.runId),
        status,
        stateRevision: nonNegative(row.stateRevision),
        runRevision: nonNegative(row.runRevision),
        failureCode: row.failureCode == null ? null : typeof row.failureCode === 'string' && SAFE.test(row.failureCode) ? row.failureCode : (() => { throw new ReconciliationResponseError() })(),
        executionMode: typeof row.executionMode === 'string' && row.executionMode.length > 0 ? row.executionMode : (() => { throw new ReconciliationResponseError() })(),
        sideEffectStartedAt: time(row.sideEffectStartedAt),
        createdAt: time(row.createdAt),
        updatedAt: time(row.updatedAt),
        attemptCount: nonNegative(row.attemptCount),
        evidenceRequired: row.evidenceRequired === true,
      }
    }),
    total: nonNegative(raw.total),
  }
}

export function listReconciliationCalls(): Promise<ReconciliationPage> {
  return apiClient.get<unknown>('/api/capability-calls/reconciliation').then(parseReconciliationPage)
}

export function reconcileCapabilityCall(callId: string, input: ReconcileInput): Promise<ReconcileResult> {
  return apiClient
    .post<unknown>(`/api/capability-calls/${callId}/reconcile`, {
      body: {
        expectedCallRevision: input.expectedCallRevision,
        expectedRunRevision: input.expectedRunRevision,
        decision: input.decision,
        evidenceArtifactIds: input.evidenceArtifactIds,
        requestId: input.requestId,
        reason: input.reason,
      },
    })
    .then((value) => {
      const raw = object(value)
      const decision = raw.decision
      if (decision !== 'mark_succeeded' && decision !== 'mark_failed' && decision !== 'mark_compensated') throw new ReconciliationResponseError()
      return {
        callId: id(raw.callId),
        decision,
        resultingCallStatus: typeof raw.resultingCallStatus === 'string' ? raw.resultingCallStatus : (() => { throw new ReconciliationResponseError() })(),
        resultingCallRevision: nonNegative(raw.resultingCallRevision),
        resultingRunRevision: nonNegative(raw.resultingRunRevision),
        reconciliationId: id(raw.reconciliationId),
        created: raw.created === true,
      }
    })
}
