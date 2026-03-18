import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Play,
  Square,
  X,
  GitBranch,
  ListTree,
  Loader2,
  Terminal,
  FileJson,
  AlertCircle,
  CheckCircle2,
  Clock,
  RefreshCcw,
  Keyboard,
  ChevronDown,
  ChevronRight,
  Wrench,
  Database,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'
import { Switch } from '@/components/ui/switch'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { HumanApprovalCard } from '@/features/shared/hitl'
import { useWorkflowEditorStore } from '../../stores/workflow-editor-store'
import {
  buildCompletedWorkflowConversationHistory,
  useWorkflowTestRunStore,
} from '../../stores/workflow-test-run-store'
import { runWorkflowTestStreamById, submitWorkflowRunApprovalDecision, validateWorkflowById } from '../../api/workflows'
import { defaultLabelForNodeType } from './labelUtils'
import { serializeToWorkflowInput } from './serialization'
import { isValidStartStructuredFieldName, normalizeStartNodeConfig } from './startNodeConfig'
import type { ContainerBodyNodeType, NodeType, StartStructuredField } from '../../api/workflow'

interface WorkflowTestRunPanelProps {
  workflowId: string
  startInputMode: 'text' | 'structured'
}

type PanelTab = 'conversation' | 'input' | 'result' | 'trace' | 'raw'
const NODE_IO_PREVIEW_LIMIT = 800

function stringifySnapshotValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return '(null)'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function previewSnapshotValue(value: unknown): string {
  const text = stringifySnapshotValue(value)
  if (text.length <= NODE_IO_PREVIEW_LIMIT) return text
  return `${text.slice(0, NODE_IO_PREVIEW_LIMIT)}...`
}

function canExpandSnapshotValue(value: unknown): boolean {
  return stringifySnapshotValue(value).length > NODE_IO_PREVIEW_LIMIT
}

function formatToolStatus(status: 'running' | 'completed' | 'error'): string {
  if (status === 'completed') return 'completed'
  if (status === 'error') return 'failed'
  return 'running'
}

function splitScopedNodeId(scoped: string): { containerId: string; nodeId: string } | null {
  const idx = scoped.indexOf('::')
  if (idx <= 0 || idx >= scoped.length - 2) return null
  return {
    containerId: scoped.slice(0, idx),
    nodeId: scoped.slice(idx + 2),
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function resolveDisplayLabel(rawLabel: unknown, rawNodeType: unknown, fallback: string): string {
  const label = `${rawLabel ?? ''}`.trim()
  if (label) return label
  const nodeType = `${rawNodeType ?? ''}`.trim() as NodeType
  return nodeType ? defaultLabelForNodeType(nodeType) : fallback
}

function extractContainerBodyNodes(config: unknown): Array<Record<string, unknown>> {
  const configRecord = asRecord(config)
  if (!configRecord) return []
  const rawNodes = configRecord.bodyNodes ?? configRecord.body_nodes
  if (!Array.isArray(rawNodes)) return []
  return rawNodes
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null)
}

function parseStructuredValue(
  field: StartStructuredField,
  rawValue: unknown,
): { ok: true; value: unknown } | { ok: false; missing: boolean } {
  if (rawValue === undefined || rawValue === null || rawValue === '') {
    return { ok: false, missing: true }
  }
  if (field.type === 'string') {
    return { ok: true, value: String(rawValue) }
  }
  if (field.type === 'number') {
    const parsed = Number(rawValue)
    if (Number.isFinite(parsed)) return { ok: true, value: parsed }
    return { ok: false, missing: false }
  }
  if (field.type === 'integer') {
    const parsed = Number(rawValue)
    if (Number.isInteger(parsed)) return { ok: true, value: parsed }
    return { ok: false, missing: false }
  }
  if (field.type === 'boolean') {
    if (rawValue === true || rawValue === 'true') return { ok: true, value: true }
    if (rawValue === false || rawValue === 'false') return { ok: true, value: false }
    return { ok: false, missing: false }
  }
  return { ok: false, missing: false }
}

export function WorkflowTestRunPanel({ workflowId, startInputMode }: WorkflowTestRunPanelProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<PanelTab>('input')
  const [expandedNodeIo, setExpandedNodeIo] = useState<Record<string, boolean>>({})
  const [expandedNodeIoFull, setExpandedNodeIoFull] = useState<Record<string, boolean>>({})
  const [expandedToolDetails, setExpandedToolDetails] = useState<Record<string, boolean>>({})
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [submittingApprovalId, setSubmittingApprovalId] = useState<string | null>(null)
  const runSubmitLockedRef = useRef(false)
  const wfStore = useWorkflowEditorStore()
  const {
    panelOpen,
    status,
    input,
    structuredInput,
    streamOutput,
    sessionId,
    sessionMemory,
    messages,
    turnsByRunId,
    selectedRunId,
    result,
    deltaSummary,
    traceEvents,
    pendingApprovals,
    nodeSnapshots,
    nodeTraceMap,
    sessionRuns,
    activeRunId,
    setPanelOpen,
    setInput,
    setStructuredInputField,
    setStreamOutput,
    beginRun,
    cancelRun,
    ingestEvent,
    markRunError,
    selectRun,
    reset,
  } = useWorkflowTestRunStore()
  const isConversationMode = startInputMode === 'text'
  const startStructuredFields = useMemo(() => {
    const startNode = wfStore.nodes.find((node) => node.data.nodeType === 'start')
    return normalizeStartNodeConfig(startNode?.data.config ?? null).structuredFields
      .filter((field) => isValidStartStructuredFieldName(field.name))
  }, [wfStore.nodes])

  const traceNodes = useMemo(
    () =>
      Object.values(nodeTraceMap).sort((a, b) => {
        const aStarted = a.startedAt ?? Number.MAX_SAFE_INTEGER
        const bStarted = b.startedAt ?? Number.MAX_SAFE_INTEGER
        if (aStarted !== bStarted) return aStarted - bStarted
        if (a.nodeId !== b.nodeId) return a.nodeId.localeCompare(b.nodeId)
        return a.executionKey.localeCompare(b.executionKey)
      }),
    [nodeTraceMap],
  )
  const traceNodeLabelMap = useMemo(() => {
    const labelMap = new Map<string, string>()

    for (const node of wfStore.nodes) {
      const nodeData = asRecord(node.data)
      const nodeId = `${node.id ?? nodeData?.nodeId ?? ''}`.trim()
      if (!nodeId) continue

      const nodeType = `${nodeData?.nodeType ?? ''}`.trim()
      const nodeLabel = resolveDisplayLabel(nodeData?.label, nodeType, nodeId)
      labelMap.set(nodeId, nodeLabel)

      if (nodeType !== 'iteration' && nodeType !== 'loop') continue

      const bodyNodes = extractContainerBodyNodes(nodeData?.config)
      for (const bodyNode of bodyNodes) {
        const innerNodeId = `${bodyNode.nodeId ?? bodyNode.node_id ?? ''}`.trim()
        if (!innerNodeId) continue
        const innerNodeType = `${bodyNode.nodeType ?? bodyNode.node_type ?? ''}`.trim() as ContainerBodyNodeType
        const innerNodeLabel = resolveDisplayLabel(bodyNode.label, innerNodeType, innerNodeId)
        labelMap.set(`${nodeId}::${innerNodeId}`, `${nodeLabel} / ${innerNodeLabel}`)
      }
    }

    return labelMap
  }, [wfStore.nodes])
  const hasAnyRunResult = useMemo(
    () =>
      status !== 'idle'
      || messages.length > 0
      || Object.keys(turnsByRunId).length > 0
      || sessionRuns.length > 0
      || result.durationMs !== null
      || Boolean(result.finalText)
      || result.finalJson !== null
      || Boolean(result.errorMessage),
    [messages.length, result.durationMs, result.errorMessage, result.finalJson, result.finalText, sessionRuns.length, status, turnsByRunId],
  )
  const selectedTurn = selectedRunId ? turnsByRunId[selectedRunId] ?? null : null

  useEffect(() => {
    if (isConversationMode && activeTab === 'input') {
      setActiveTab('conversation')
      return
    }
    if (!isConversationMode && activeTab === 'conversation') {
      setActiveTab('input')
    }
  }, [activeTab, isConversationMode])

  const handleSubmitApprovalDecision = async (
    approvalId: string,
    payload: { decision: 'approved' | 'rejected'; values: Record<string, unknown>; comment?: string },
  ) => {
    if (!activeRunId) {
      toast.error(t('settings.skills.humanApproval.noActiveRun'))
      return
    }
    setSubmittingApprovalId(approvalId)
    try {
      const approval = await submitWorkflowRunApprovalDecision(activeRunId, approvalId, payload)
      ingestEvent({
        event: 'human_approval_resolved',
        data: {
          runId: activeRunId,
          approval,
          ts: new Date().toISOString(),
        },
      })
      toast.success(t('settings.skills.humanApproval.submitted'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('settings.skills.humanApproval.submitFailed')
      toast.error(message)
    } finally {
      setSubmittingApprovalId(null)
    }
  }

  const selectNodeFromTrace = (nodeId: string) => {
    const scoped = splitScopedNodeId(nodeId)
    if (scoped) {
      wfStore.setSelectedSubflowSelection(scoped.containerId, scoped.nodeId, null)
      wfStore.requestFocusNode(scoped.containerId)
      return
    }
    wfStore.setSelectedNodeId(nodeId)
    wfStore.requestFocusNode(nodeId)
  }

  const handleRun = async () => {
    if (runSubmitLockedRef.current || status === 'running') return
    runSubmitLockedRef.current = true

    try {
      if (!workflowId) return
      const workflow = serializeToWorkflowInput(wfStore.nodes, wfStore.edges, wfStore.viewport)
      let validation
      try {
        validation = await validateWorkflowById(workflowId, workflow)
      } catch (error) {
        const message = error instanceof Error ? error.message : '校验失败'
        toast.error(message)
        markRunError(message)
        return
      }
      if (!validation.valid) {
        const message = validation.errors.map((item) => item.message).slice(0, 3).join('; ') || 'Workflow validation failed'
        toast.error(message)
        markRunError(message)
        return
      }

      const payload: {
        workflow: ReturnType<typeof serializeToWorkflowInput>
        userInput?: string
        structuredInput?: Record<string, unknown>
        sessionId?: string
        history?: Array<{ role: 'user' | 'assistant'; content: string }>
        sessionMemory?: { conversationSummary?: string; skillFacts?: string[] }
        streamOutput: boolean
      } = {
        workflow,
        streamOutput,
      }

      if (startInputMode === 'structured') {
        const structuredPayload: Record<string, unknown> = {}
        for (const field of startStructuredFields) {
          const parsed = parseStructuredValue(field, structuredInput[field.name])
          if (!parsed.ok) {
            if (parsed.missing) {
              if (field.required) {
                toast.error(t('settings.skills.structuredInputInvalid'))
                return
              }
              continue
            }
            toast.error(t('settings.skills.structuredInputInvalid'))
            return
          }
          structuredPayload[field.name] = parsed.value
        }
        payload.structuredInput = structuredPayload
      } else {
        const userInput = input.trim()
        if (!userInput) {
          toast.error('请输入测试输入')
          return
        }
        payload.userInput = userInput
        payload.sessionId = sessionId
        payload.history = buildCompletedWorkflowConversationHistory(messages)
        if (sessionMemory.conversationSummary || sessionMemory.skillFacts.length > 0) {
          payload.sessionMemory = {
            conversationSummary: sessionMemory.conversationSummary,
            skillFacts: sessionMemory.skillFacts,
          }
        }
      }

      const controller = new AbortController()
      beginRun(controller, {
        mode: startInputMode,
        submittedInput: payload.userInput,
      })
      setPanelOpen(true)
      setActiveTab(startInputMode === 'text' ? 'conversation' : 'trace')

      try {
        await runWorkflowTestStreamById(
          workflowId,
          payload,
          {
            signal: controller.signal,
            onEvent: (event) => {
              if (controller.signal.aborted) return
              ingestEvent(event)
            },
          },
        )
      } catch (error) {
        const isAbort = error instanceof Error && error.name === 'AbortError'
        if (isAbort) return
        const message = error instanceof Error ? error.message : 'Workflow test run failed'
        markRunError(message)
        toast.error(message)
      }
    } finally {
      runSubmitLockedRef.current = false
    }
  }

  return (
    panelOpen ? (
      <div className="fixed right-6 top-[78px] z-50 w-[480px] h-[640px] max-w-[calc(100vw-3rem)] max-h-[calc(100vh-6rem)] rounded-xl border bg-white/95 backdrop-blur-sm shadow-2xl flex flex-col overflow-hidden animate-in slide-in-from-top-4 duration-200">
        {/* Header */}
        <div className="px-5 py-4 border-b bg-muted/30 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 bg-primary/10 rounded-lg text-primary">
              <Terminal className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold leading-none text-foreground">工作流测试</h3>
              <p className="text-[11px] text-muted-foreground mt-1">草稿直跑，不计入正式会话</p>
            </div>
          </div>

          {/* Status in Header */}
          <div className="ml-auto flex items-center gap-2 mr-4">
            {status === 'running' && (
              <div className="flex items-center gap-1.5 text-[10px] text-primary bg-primary/10 px-2 py-1 rounded-full border border-primary/20">
                <Loader2 className="w-3 h-3 animate-spin" />
                Running
              </div>
            )}
            {status === 'completed' && (
              <div className="flex items-center gap-1.5 text-[10px] text-green-600 bg-green-50 px-2 py-1 rounded-full border border-green-100">
                <CheckCircle2 className="w-3 h-3" />
                Completed
                {result.durationMs && ` (${result.durationMs}ms)`}
              </div>
            )}
            {status === 'error' && (
              <div className="flex items-center gap-1.5 text-[10px] text-red-600 bg-red-50 px-2 py-1 rounded-full border border-red-100">
                <AlertCircle className="w-3 h-3" />
                Failed
              </div>
            )}
          </div>
          <button
            onClick={() => setPanelOpen(false)}
            className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tabs Bar */}
        <div className="px-3 py-2 border-b flex items-center gap-1 bg-white shrink-0">
          {[
            ...(isConversationMode ? [{ id: 'conversation', label: '对话', icon: Keyboard }] : [{ id: 'input', label: '输入', icon: Keyboard }]),
            { id: 'result', label: '结果', icon: FileJson },
            { id: 'trace', label: '追踪', icon: ListTree },
            { id: 'raw', label: '原始', icon: GitBranch },
          ].map((tab) => {
            const Icon = tab.icon
            const disabled = tab.id !== 'input' && tab.id !== 'conversation' && !hasAnyRunResult
            return (
              <button
                key={tab.id}
                onClick={() => !disabled && setActiveTab(tab.id as PanelTab)}
                disabled={disabled}
                className={`
                  relative flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors rounded-md
                  ${activeTab === tab.id
                    ? 'bg-primary/10 text-primary'
                    : disabled
                      ? 'text-muted-foreground/50 cursor-not-allowed'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }
                `}
              >
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            )
          })}
        </div>

        <div className="flex-1 overflow-auto p-4 custom-scrollbar bg-slate-50/50">
          {activeTab === 'conversation' && isConversationMode && (
            <div className="h-full flex flex-col gap-4">
              <div className="flex-1 overflow-auto min-h-0 space-y-4 pr-1">
                {messages.length === 0 ? (
                  <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center text-muted-foreground">
                    <Keyboard className="w-8 h-8 mb-3 opacity-20" />
                    <p className="text-sm font-medium text-foreground">连续试运行对话</p>
                  </div>
                ) : (
                  messages.map((message) => {
                    if (message.role === 'user') {
                      return (
                        <div key={message.id} className="flex justify-end">
                          <div className="max-w-[85%] rounded-2xl rounded-tr-[4px] bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground shadow-sm whitespace-pre-wrap">
                            {message.content}
                          </div>
                        </div>
                      )
                    }

                    const isSelected = Boolean(message.runId && message.runId === selectedRunId)
                    const assistantContent = message.finalJson !== undefined && message.finalJson !== null
                      ? JSON.stringify(message.finalJson, null, 2)
                      : (message.content || message.errorMessage || '')

                    return (
                      <button
                        key={message.id}
                        type="button"
                        disabled={!message.runId || status === 'running'}
                        onClick={() => {
                          if (!message.runId) return
                          selectRun(message.runId)
                        }}
                        className={`w-full text-left rounded-2xl border bg-white/80 p-4 shadow-sm transition-all ${
                          isSelected ? 'border-primary/40 ring-2 ring-primary/10' : 'border-border/60 hover:border-primary/20'
                        } ${!message.runId || status === 'running' ? 'cursor-default' : 'cursor-pointer'}`}
                      >
                        <div className="flex items-start gap-3">
                          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10 text-primary">
                            <Terminal className="w-4 h-4" />
                          </div>
                          <div className="min-w-0 flex-1 space-y-2">
                            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                              <span className="font-medium text-foreground">Assistant</span>
                              {message.runId && (
                                <span className="rounded-full bg-slate-100 px-2 py-0.5 font-mono">
                                  {message.runId.slice(0, 8)}
                                </span>
                              )}
                              <span className={`rounded-full px-2 py-0.5 ${
                                message.status === 'completed'
                                  ? 'bg-green-50 text-green-700'
                                  : message.status === 'error'
                                    ? 'bg-red-50 text-red-700'
                                    : message.status === 'cancelled'
                                      ? 'bg-slate-100 text-slate-600'
                                      : 'bg-blue-50 text-blue-700'
                              }`}>
                                {message.status}
                              </span>
                              {isSelected && (
                                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-primary">
                                  当前查看
                                </span>
                              )}
                            </div>
                            <div className="rounded-xl border bg-slate-50/70 px-3 py-2.5">
                              {message.status === 'running' && !assistantContent ? (
                                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                  正在生成...
                                </div>
                              ) : message.finalJson !== undefined && message.finalJson !== null ? (
                                <pre className="whitespace-pre-wrap break-words text-xs font-mono text-slate-700">
                                  {assistantContent}
                                </pre>
                              ) : (
                                <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700">
                                  {assistantContent || '(empty output)'}
                                </div>
                              )}
                            </div>
                            {message.runId && status !== 'running' && (
                              <div className="text-[11px] text-muted-foreground">
                                点击此回复可切换下方 Result / Trace / Raw 到本轮
                              </div>
                            )}
                          </div>
                        </div>
                      </button>
                    )
                  })
                )}
              </div>

              <div className="shrink-0 rounded-xl border bg-white p-3 shadow-sm space-y-3">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.nativeEvent.isComposing || e.repeat) return
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      if (status !== 'running' && !runSubmitLockedRef.current) {
                        void handleRun()
                      }
                    }
                  }}
                  disabled={status === 'running'}
                  placeholder="继续输入测试内容..."
                  className="min-h-[88px] w-full resize-none rounded-lg border bg-background px-4 py-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all font-mono"
                />
                <div className="flex items-center justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-2">
                      <Switch
                        id="conversation-stream-mode"
                        checked={streamOutput}
                        onCheckedChange={setStreamOutput}
                        disabled={status === 'running'}
                        className="data-[state=checked]:bg-primary scale-90 origin-left"
                      />
                      <label
                        htmlFor="conversation-stream-mode"
                        className="text-xs text-muted-foreground cursor-pointer select-none"
                      >
                        流式输出
                      </label>
                    </div>
                    <span className="text-[11px] text-muted-foreground">
                      {messages.length} message{messages.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setResetDialogOpen(true)}
                      className="p-2 text-muted-foreground hover:text-foreground rounded-lg hover:bg-muted transition-colors"
                      title="重置所有内容"
                    >
                      <RefreshCcw className="w-4 h-4" />
                    </button>
                    {status === 'running' ? (
                      <button
                        onClick={cancelRun}
                        className="flex items-center gap-2 px-4 py-2 bg-red-50 text-red-600 hover:bg-red-100 rounded-lg text-xs font-medium transition-colors"
                      >
                        <Square className="w-3.5 h-3.5 fill-current" />
                        停止
                      </button>
                    ) : (
                      <button
                        onClick={() => void handleRun()}
                        disabled={!input.trim()}
                        className="flex items-center gap-2 px-6 py-2 bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg text-xs font-medium shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed active:scale-95"
                      >
                        <Play className="w-3.5 h-3.5 fill-current" />
                        发送
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'input' && !isConversationMode && (
            <div className="h-full flex flex-col gap-4">
              <div className="flex-1 flex flex-col gap-2 min-h-0">
                <div className="flex items-center justify-between shrink-0">
                  <label className="text-xs font-medium text-foreground">
                    {t('settings.skills.structuredInputRunPanelTitle')}
                  </label>
                </div>
                {startInputMode === 'structured' ? (
                  <div className="flex-1 overflow-auto space-y-2 rounded-lg border bg-white px-3 py-3">
                    {startStructuredFields.length === 0 && (
                      <div className="text-xs text-muted-foreground">{t('settings.skills.structuredInputInvalid')}</div>
                    )}
                    {startStructuredFields.map((field) => (
                      <div key={field.name} className="space-y-1">
                        <label className="text-[11px] font-medium text-foreground/80">
                          {field.name}
                          {field.required ? ' *' : ''}
                        </label>
                        {field.type === 'boolean' ? (
                          <select
                            value={structuredInput[field.name] === true ? 'true' : structuredInput[field.name] === false ? 'false' : ''}
                            onChange={(e) => {
                              const value = e.target.value
                              setStructuredInputField(field.name, value === '' ? '' : value === 'true')
                            }}
                            className="w-full px-2 py-1.5 text-xs rounded border bg-background"
                          >
                            <option value="">-</option>
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        ) : (
                          <input
                            type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                            step={field.type === 'integer' ? '1' : 'any'}
                            value={String(structuredInput[field.name] ?? '')}
                            onChange={(e) => setStructuredInputField(field.name, e.target.value)}
                            className="w-full px-2 py-1.5 text-xs rounded border bg-background"
                          />
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="输入测试内容..."
                    className="flex-1 w-full resize-none rounded-lg border bg-white px-4 py-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all font-mono"
                  />
                )}
              </div>

              {/* Controls */}
              <div className="flex items-center justify-between gap-4 shrink-0 bg-white p-3 rounded-lg border shadow-sm">
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <Switch
                      id="stream-mode"
                      checked={streamOutput}
                      onCheckedChange={setStreamOutput}
                      disabled={status === 'running'}
                      className="data-[state=checked]:bg-primary scale-90 origin-left"
                    />
                    <label
                      htmlFor="stream-mode"
                      className="text-xs text-muted-foreground cursor-pointer select-none"
                    >
                      流式输出
                    </label>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setResetDialogOpen(true)}
                    className="p-2 text-muted-foreground hover:text-foreground rounded-lg hover:bg-muted transition-colors"
                    title="重置所有内容"
                  >
                    <RefreshCcw className="w-4 h-4" />
                  </button>
                  {status === 'running' ? (
                    <button
                      onClick={cancelRun}
                      className="flex items-center gap-2 px-4 py-2 bg-red-50 text-red-600 hover:bg-red-100 rounded-lg text-xs font-medium transition-colors"
                    >
                      <Square className="w-3.5 h-3.5 fill-current" />
                      停止
                    </button>
                  ) : (
                    <button
                      onClick={() => void handleRun()}
                      className="flex items-center gap-2 px-6 py-2 bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg text-xs font-medium shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed active:scale-95"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" />
                      运行
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
          {activeTab === 'result' && (
            <div className="space-y-4">
              {!hasAnyRunResult ? (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                  <Play className="w-8 h-8 mb-3 opacity-20" />
                  <p className="text-xs">配置输入并点击运行以查看结果</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {isConversationMode && selectedTurn && (
                    <div className="flex items-center gap-2 text-[10px] text-muted-foreground bg-slate-100/70 px-3 py-1.5 rounded-md border border-slate-200">
                      <Clock className="w-3 h-3" />
                      当前查看轮次：{selectedTurn.runId.slice(0, 8)}
                    </div>
                  )}
                  {result.finalJson !== null && (
                    <div className="flex items-center gap-2 text-[10px] text-blue-600 bg-blue-50/50 px-3 py-1.5 rounded-md border border-blue-100">
                      <FileJson className="w-3 h-3" />
                      Structure Output Detected
                    </div>
                  )}
                  <div className="relative group">
                    <pre className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-words bg-white border rounded-lg p-4 shadow-sm text-slate-700">
                      {result.finalJson !== null ? JSON.stringify(result.finalJson, null, 2) : (result.finalText || '(empty output)')}
                    </pre>
                  </div>
                </div>
              )}

              {result.errorMessage && (
                <div className="rounded-lg border border-red-200 bg-red-50/50 p-4 text-xs">
                  <div className="flex items-start gap-2 text-red-700 font-medium mb-1">
                    <AlertCircle className="w-3.5 h-3.5 mt-0.5" />
                    Execution Failed
                  </div>
                  <p className="text-red-600 pl-5.5">{result.errorMessage}</p>
                </div>
              )}

              {!isConversationMode && sessionRuns.length > 0 && (
                <div className="pt-4 border-t">
                  <div className="text-xs font-medium text-muted-foreground mb-3 flex items-center gap-2">
                    <Clock className="w-3.5 h-3.5" />
                    Recent Runs
                  </div>
                  <div className="space-y-2">
                    {sessionRuns.map((run) => (
                      <div key={run.runId} className="group relative text-xs rounded-lg border bg-white p-3 hover:border-primary/30 transition-colors">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-mono text-[10px] text-muted-foreground">{run.runId.slice(0, 8)}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${run.status === 'completed' ? 'bg-green-50 text-green-600' :
                            run.status === 'error' ? 'bg-red-50 text-red-600' : 'bg-slate-100 text-slate-600'
                            }`}>
                            {run.status}
                          </span>
                        </div>
                        <div className="line-clamp-2 text-muted-foreground group-hover:text-foreground transition-colors">
                          {run.finalText || '(empty)'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'trace' && (
            <div className="space-y-4">
              {pendingApprovals.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs font-semibold text-foreground">
                    {t('settings.skills.humanApproval.pendingTitle')}
                  </div>
                  <div className="space-y-2">
                    {pendingApprovals.map((approval) => (
                      <HumanApprovalCard
                        key={approval.id}
                        approval={approval}
                        submitting={submittingApprovalId === approval.id}
                        onSubmit={(payload) => handleSubmitApprovalDecision(approval.id, payload)}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Node Execution Status */}
              <div className="space-y-2">
                {traceNodes.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                    <ListTree className="w-8 h-8 mb-3 opacity-20" />
                    <p className="text-xs">暂无节点执行轨迹</p>
                  </div>
                )}

                {traceNodes.map((node) => {
                  const snapshot = nodeSnapshots[node.executionKey]
                  const ioExpanded = !!expandedNodeIo[node.executionKey]
                  const ioFullExpanded = !!expandedNodeIoFull[node.executionKey]
                  const nodeDisplayName = traceNodeLabelMap.get(node.nodeId) ?? node.nodeId

                  return (
                    <div key={node.executionKey} className="group rounded-lg border bg-white shadow-sm overflow-hidden transition-all hover:shadow-md">
                      <div className="px-3 py-2.5 flex items-center justify-between gap-3 bg-slate-50/50">
                        <div className="flex items-center gap-3">
                          <div className={`w-1.5 h-1.5 rounded-full ${node.status === 'success' ? 'bg-green-500' :
                            node.status === 'running' ? 'bg-blue-500 animate-pulse' :
                              node.status === 'error' ? 'bg-red-500' : 'bg-slate-300'
                            }`} />
                          <div>
                            <div className="text-xs font-semibold text-foreground flex items-center gap-2">
                              {nodeDisplayName}
                              {node.durationMs && (
                                <span className="text-[10px] font-normal text-muted-foreground bg-slate-100 px-1.5 py-0.5 rounded">
                                  {node.durationMs}ms
                                </span>
                              )}
                              {node.nodeExecutionId && (
                                <span className="text-[10px] font-normal text-muted-foreground bg-slate-100 px-1.5 py-0.5 rounded font-mono">
                                  {node.nodeExecutionId.slice(0, 8)}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => selectNodeFromTrace(node.nodeId)}
                            className="text-[10px] text-muted-foreground hover:text-primary px-2 py-1 hover:bg-white rounded border border-transparent hover:border-slate-200 transition-all"
                          >
                            Locate
                          </button>
                          {snapshot && (
                            <button
                              onClick={() => setExpandedNodeIo(prev => ({ ...prev, [node.executionKey]: !prev[node.executionKey] }))}
                              className={`text-[10px] px-2 py-1 rounded border transition-all ${ioExpanded
                                ? 'bg-primary/5 text-primary border-primary/20'
                                : 'text-muted-foreground hover:text-primary hover:bg-white hover:border-slate-200'
                                }`}
                            >
                              IO Details
                            </button>
                          )}
                        </div>
                      </div>

                      {snapshot && ioExpanded && (
                        <div className="px-3 py-3 border-t space-y-3 bg-white animate-in slide-in-from-top-1">
                          <div className="grid gap-3">
                            <div className="space-y-1.5">
                              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Input</div>
                              <div className="bg-slate-50 rounded border px-3 py-2">
                                <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-slate-600">
                                  {ioFullExpanded ? stringifySnapshotValue(snapshot.input) : previewSnapshotValue(snapshot.input)}
                                </pre>
                              </div>
                            </div>

                            <div className="space-y-1.5">
                              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Output</div>
                              <div className="bg-slate-50 rounded border px-3 py-2">
                                <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-slate-600">
                                  {ioFullExpanded ? stringifySnapshotValue(snapshot.output) : previewSnapshotValue(snapshot.output)}
                                </pre>
                              </div>
                            </div>
                          </div>

                          {snapshot.errorMessage && (
                            <div className="rounded border border-red-200 bg-red-50/50 px-3 py-2 text-[10px] text-red-700 flex items-start gap-2">
                              <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
                              {snapshot.errorMessage}
                            </div>
                          )}

                          {canExpandSnapshotValue(snapshot.input) || canExpandSnapshotValue(snapshot.output) ? (
                            <button
                              onClick={() => setExpandedNodeIoFull(prev => ({ ...prev, [node.executionKey]: !prev[node.executionKey] }))}
                              className="w-full text-center text-[10px] text-muted-foreground hover:text-primary py-1 hover:bg-slate-50 rounded transition-colors"
                            >
                              {ioFullExpanded ? 'Show Less' : 'Show Full Content'}
                            </button>
                          ) : null}
                        </div>
                      )}

                      {node.nodeType === 'agent' && node.toolCalls && node.toolCalls.length > 0 && (
                        <div className="border-t bg-white px-3 py-3 space-y-2">
                          <div className="flex items-center gap-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                            <Wrench className="w-3 h-3" />
                            Tool Chain
                          </div>
                          <div className="space-y-2">
                            {node.toolCalls.map((toolCall) => {
                              const toolKey = `${node.executionKey}:${toolCall.id}`
                              const expanded = !!expandedToolDetails[toolKey]
                              return (
                                <div key={toolCall.id} className="rounded border bg-slate-50/70">
                                  <button
                                    onClick={() => setExpandedToolDetails((prev) => ({ ...prev, [toolKey]: !prev[toolKey] }))}
                                    className="w-full px-3 py-2 flex items-center justify-between gap-3 text-left"
                                  >
                                    <div className="min-w-0 flex items-center gap-2">
                                      {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
                                      <span className="text-[11px] text-foreground truncate">
                                        {`Round ${toolCall.agentRound ?? '?'} · ${toolCall.name} · ${formatToolStatus(toolCall.status)}${typeof toolCall.durationMs === 'number' ? ` · ${toolCall.durationMs}ms` : ''}`}
                                      </span>
                                    </div>
                                    {toolCall.toolKind === 'knowledge' ? (
                                      <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] text-blue-700">
                                        <Database className="w-3 h-3" />
                                        Knowledge
                                      </span>
                                    ) : (
                                      <span className="inline-flex items-center rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-600">
                                        Tool
                                      </span>
                                    )}
                                  </button>
                                  {expanded && (
                                    <div className="border-t px-3 py-2 space-y-2">
                                      <div className="grid gap-2">
                                        <div className="space-y-1">
                                          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Args</div>
                                          <div className="rounded border bg-white px-2.5 py-2">
                                            <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-slate-600">
                                              {previewSnapshotValue(toolCall.args ?? {})}
                                            </pre>
                                          </div>
                                        </div>
                                        {toolCall.result !== undefined && (
                                          <div className="space-y-1">
                                            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Result</div>
                                            <div className="rounded border bg-white px-2.5 py-2">
                                              <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-slate-600">
                                                {previewSnapshotValue(toolCall.result)}
                                              </pre>
                                            </div>
                                          </div>
                                        )}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {activeTab === 'raw' && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs text-muted-foreground mb-2">
                <span>raw_events.json</span>
                <span className="font-mono">{traceEvents.length} events</span>
              </div>
              <pre className="rounded-lg border bg-slate-900 text-slate-50 p-4 text-[10px] font-mono whitespace-pre-wrap break-words">
                {JSON.stringify({
                  keyEvents: traceEvents,
                  deltaSummary,
                  nodeSnapshots,
                }, null, 2)}
              </pre>
            </div>
          )}
        </div>

        <ConfirmDialog
          isOpen={resetDialogOpen}
          title="重置测试"
          description="确定要重置所有测试内容吗？此操作将清空输入和所有执行结果，且无法撤销。"
          confirmText="重置"
          cancelText="取消"
          variant="destructive"
          onConfirm={() => {
            reset()
            setActiveTab(isConversationMode ? 'conversation' : 'input')
            setResetDialogOpen(false)
          }}
          onCancel={() => setResetDialogOpen(false)}
        />
      </div >
    ) : null
  )
}
