import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { ArrowLeft, Save, Undo2, Redo2, Loader2, LayoutTemplate, Play, ListChecks, AlertCircle, Send, History, SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'
import { isApiError } from '@/lib/api/client'
import {
  clearWorkflowVersions,
  deleteWorkflowVersion,
  getWorkflow,
  listWorkflowVersions,
  publishWorkflow,
  rollbackWorkflowVersion,
  saveWorkflowById,
  validateWorkflowById,
} from '../api/workflows'
import type { NodeConfig, WorkflowInput } from '../api/workflow'
import { getSystemToolDefinitions, getToolsWithParams } from '../api/tools'
import { useWorkflowEditorStore } from '../stores/workflow-editor-store'
import { useWorkflowTestRunStore } from '../stores/workflow-test-run-store'
import { FlowCanvas } from '../components/workflow/FlowCanvas'
import { NodePalette } from '../components/workflow/NodePalette'
import { PropertyPanel } from '../components/workflow/PropertyPanel'
import { WorkflowTestRunPanel } from '../components/workflow/WorkflowTestRunPanel'
import { WorkflowValidationChecklistPanel } from '../components/workflow/WorkflowValidationChecklistPanel'
import { WorkflowEnvVarPanel } from '../components/workflow/WorkflowEnvVarPanel'
import { serializeToWorkflowInput, deserializeFromWorkflow } from '../components/workflow/serialization'
import { PublishVersionDialog } from '../components/versioning/PublishVersionDialog'
import { TargetVersionPanel } from '../components/versioning/TargetVersionPanel'
import {
  buildValidationSignature,
  computeDeadEndWarnings,
  normalizeValidationIssues,
  type WorkflowValidationIssue,
} from '../components/workflow/workflowValidation'
import type { WorkflowToolDefinition } from '../components/workflow/types'
import { autoLayoutWorkflowWithSubflows } from '../components/workflow/autoLayout'
import { normalizeStartNodeConfig } from '../components/workflow/startNodeConfig'
import { getStartNodeFromNodes, getWorkflowEnvVarsFromNodes, toStartConfigWithEnvVars } from '../components/workflow/workflowEnvVars'


import { Tooltip } from '../../../components/ui/Tooltip'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '../../../components/ui/hover-card'

import '@xyflow/react/dist/style.css'

export default function WorkflowEditorPage() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const qc = useQueryClient()
  const initialized = useRef(false)

  const store = useWorkflowEditorStore()
  const testRunPanelOpen = useWorkflowTestRunStore((s) => s.panelOpen)
  const setTestRunPanelOpen = useWorkflowTestRunStore((s) => s.setPanelOpen)
  const [validationPanelOpen, setValidationPanelOpen] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [validationErrors, setValidationErrors] = useState<WorkflowValidationIssue[]>([])
  const [validationWarnings, setValidationWarnings] = useState<WorkflowValidationIssue[]>([])
  const [validationRequestError, setValidationRequestError] = useState<string | null>(null)
  const [lastValidatedAt, setLastValidatedAt] = useState<number | null>(null)
  const [workflowDescriptionDraft, setWorkflowDescriptionDraft] = useState('')
  const [versionPanelOpen, setVersionPanelOpen] = useState(false)
  const [envPanelOpen, setEnvPanelOpen] = useState(false)
  const [publishDialogOpen, setPublishDialogOpen] = useState(false)
  const validationSeqRef = useRef(0)
  const debounceTimerRef = useRef<number | null>(null)
  const latestSignatureRef = useRef('')
  const workflowSnapshotRef = useRef<WorkflowInput | null>(null)

  const { data: workflowEntity, isLoading } = useQuery({
    queryKey: ['assistant-workflow', workflowId],
    queryFn: () => getWorkflow(workflowId!),
    enabled: !!workflowId,
  })
  const {
    data: workflowVersions,
    isFetching: versionsLoading,
    error: versionsError,
    refetch: refetchVersions,
  } = useQuery({
    queryKey: ['assistant-workflow-versions', workflowId],
    queryFn: () => listWorkflowVersions(workflowId!),
    enabled: !!workflowId && versionPanelOpen,
  })
  const { data: systemToolDefs = [] } = useQuery({
    queryKey: ['assistant-system-tool-definitions-workflow'],
    queryFn: () => getSystemToolDefinitions({ includeDisabled: false, includeSchema: false }),
  })
  const { data: customTools = [] } = useQuery({
    queryKey: ['assistant-tools-workflow'],
    queryFn: () => getToolsWithParams({ includeDisabled: false }),
  })

  const workflowTools = useMemo<WorkflowToolDefinition[]>(() => {
    const merged = new Map<string, WorkflowToolDefinition>()

    systemToolDefs.forEach((tool) => {
      if (!tool.enabled) return
      merged.set(tool.name, {
        name: tool.name,
        description: tool.description,
        inputParams: tool.inputParams ?? [],
        outputParams: tool.outputParams ?? [],
      })
    })

    customTools.forEach((tool) => {
      merged.set(tool.name, {
        name: tool.name,
        description: tool.description,
        inputParams: tool.inputParams ?? [],
        outputParams: tool.outputParams ?? [],
      })
    })

    return Array.from(merged.values()).sort((a, b) => a.name.localeCompare(b.name))
  }, [customTools, systemToolDefs])

  const workflowInput = useMemo(
    () => serializeToWorkflowInput(store.nodes, store.edges, store.viewport),
    [store.edges, store.nodes, store.viewport],
  )
  const defaultPublishVersionName = useMemo(
    () => new Date().toLocaleString(),
    [publishDialogOpen],
  )
  const validationSignature = useMemo(
    () => buildValidationSignature(workflowInput),
    [workflowInput],
  )
  const startInputMode = useMemo(() => {
    const startNode = store.nodes.find((node) => node.data.nodeType === 'start')
    return normalizeStartNodeConfig(startNode?.data.config ?? null).inputMode
  }, [store.nodes])
  const workflowEnvVars = useMemo(
    () => getWorkflowEnvVarsFromNodes(store.nodes),
    [store.nodes],
  )
  const hasUnsavedChanges = useMemo(
    () => store.isDirty || workflowDescriptionDraft !== (workflowEntity?.description ?? ''),
    [store.isDirty, workflowDescriptionDraft, workflowEntity?.description],
  )





  // Load workflow data into store on first fetch
  useEffect(() => {
    if (workflowEntity && !initialized.current) {
      initialized.current = true
      const { nodes, edges, viewport } = deserializeFromWorkflow(workflowEntity)
      store.loadWorkflow(nodes, edges, viewport)
      setWorkflowDescriptionDraft(workflowEntity.description ?? '')
    }
  }, [workflowEntity])

  useEffect(() => {
    if (!workflowEntity) return
    if (store.isDirty) return
    setWorkflowDescriptionDraft(workflowEntity.description ?? '')
  }, [workflowEntity?.id, workflowEntity?.description, store.isDirty])

  useEffect(() => {
    setValidationErrors([])
    setValidationWarnings([])
    setValidationRequestError(null)
    setLastValidatedAt(null)
    setValidationPanelOpen(false)
    setIsValidating(false)
    validationSeqRef.current = 0
    latestSignatureRef.current = ''
    workflowSnapshotRef.current = null
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }
  }, [workflowId])

  const runWorkflowValidation = useCallback(
    async (input: WorkflowInput, signature: string, force = false) => {
      if (!workflowId) return
      if (!initialized.current) return
      if (!force && signature === latestSignatureRef.current) return

      if (debounceTimerRef.current) {
        window.clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }

      const seq = validationSeqRef.current + 1
      validationSeqRef.current = seq
      workflowSnapshotRef.current = input

      setIsValidating(true)
      setValidationRequestError(null)

      try {
        const validation = await validateWorkflowById(workflowId, input)
        if (seq !== validationSeqRef.current) return

        const warningMessage = t('settings.skills.workflowValidationDeadEndWarning')
        const reachabilityWarnings = computeDeadEndWarnings(input, warningMessage)
        const normalized = normalizeValidationIssues(validation.errors, reachabilityWarnings)
        setValidationErrors(normalized.errors)
        setValidationWarnings(normalized.warnings)
        setLastValidatedAt(Date.now())
        setValidationRequestError(null)
        latestSignatureRef.current = signature
      } catch (err) {
        if (seq !== validationSeqRef.current) return
        const message = err instanceof Error
          ? err.message
          : t('settings.skills.workflowValidationChecklistRequestFailed')
        setValidationRequestError(message)
      } finally {
        if (seq === validationSeqRef.current) {
          setIsValidating(false)
        }
      }
    },
    [workflowId, t],
  )

  useEffect(() => {
    if (!workflowId) return
    if (!initialized.current) return
    if (validationSignature === latestSignatureRef.current) return
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }
    debounceTimerRef.current = window.setTimeout(() => {
      void runWorkflowValidation(workflowInput, validationSignature)
    }, 500)
    return () => {
      if (debounceTimerRef.current) {
        window.clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }
    }
  }, [workflowId, runWorkflowValidation, validationSignature, workflowInput])

  const handleValidateNow = useCallback(() => {
    setValidationPanelOpen((prev) => !prev)
    void runWorkflowValidation(workflowInput, validationSignature, true)
  }, [runWorkflowValidation, validationSignature, workflowInput])

  const handleValidationRefresh = useCallback(() => {
    void runWorkflowValidation(workflowInput, validationSignature, true)
  }, [runWorkflowValidation, validationSignature, workflowInput])

  const handleLocateValidationIssue = useCallback((issue: WorkflowValidationIssue) => {
    if (!issue.nodeId) return
    const exists = store.nodes.some((node) => node.id === issue.nodeId)
    if (!exists) return
    if (issue.subflowNodeId) {
      store.setSelectedSubflowSelection(issue.nodeId, issue.subflowNodeId, null)
      store.requestFocusNode(issue.nodeId)
      return
    }
    store.setSelectedNodeId(issue.nodeId)
    store.requestFocusNode(issue.nodeId)
  }, [store])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const input = serializeToWorkflowInput(store.nodes, store.edges, store.viewport)
      const validation = await validateWorkflowById(workflowId!, input)
      if (!validation.valid) {
        const msg = validation.errors.map((e) => e.message).slice(0, 3).join('; ')
        throw new Error(msg || t('settings.skills.workflowValidationFailed'))
      }
      return saveWorkflowById(workflowId!, {
        workflow: input,
        description: workflowDescriptionDraft,
      })
    },
    onSuccess: () => {
      store.resetDirty()
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      toast.success(t('settings.skills.workflowSaved'))
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('settings.skills.workflowSaveError')
      toast.error(message)
    },
  })

  const publishMutation = useMutation({
    mutationFn: async (versionName: string) => {
      const input = serializeToWorkflowInput(store.nodes, store.edges, store.viewport)
      const validation = await validateWorkflowById(workflowId!, input)
      if (!validation.valid) {
        const msg = validation.errors.map((e) => e.message).slice(0, 3).join('; ')
        throw new Error(msg || t('settings.skills.workflowValidationFailed'))
      }
      return publishWorkflow(workflowId!, {
        workflow: input,
        description: workflowDescriptionDraft,
        versionName: versionName.trim() || undefined,
      })
    },
    onSuccess: () => {
      store.resetDirty()
      setPublishDialogOpen(false)
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      toast.success(t('settings.skills.versioning.publishSuccess'))
    },
    onError: (err) => {
      const message = isApiError(err) && err.code === 42209
        ? t('settings.skills.versioning.publishBlockedByValidation')
        : (err instanceof Error ? err.message : t('settings.skills.workflowSaveError'))
      toast.error(message)
    },
  })

  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => rollbackWorkflowVersion(workflowId!, versionId),
    onSuccess: (payload) => {
      if (payload.workflow) {
        const restored = deserializeFromWorkflow({
          id: workflowId!,
          name: workflowEntity?.name ?? '',
          description: workflowEntity?.description ?? '',
          isSystem: Boolean(workflowEntity?.isSystem),
          enabled: true,
          workflowVersion: workflowEntity?.workflowVersion ?? 1,
          workflowViewport: payload.workflow.viewport ?? null,
          nodes: (payload.workflow.nodes ?? []) as any,
          edges: (payload.workflow.edges ?? []) as any,
          draftVersionId: payload.draftVersionId,
          publishedVersionId: payload.publishedVersionId,
          referencedSkillIds: workflowEntity?.referencedSkillIds ?? [],
          referenceCount: workflowEntity?.referenceCount ?? 0,
          createdAt: workflowEntity?.createdAt ?? new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        } as any)
        store.loadWorkflow(restored.nodes, restored.edges, restored.viewport)
        store.resetDirty()
      }
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      toast.success(t('settings.skills.versioning.restoreSuccess'))
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('messages.error')
      toast.error(message)
    },
  })

  const deleteVersionMutation = useMutation({
    mutationFn: async (versionId: string) => deleteWorkflowVersion(workflowId!, versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      toast.success(t('settings.skills.versioning.deleteSuccess'))
    },
    onError: (err) => {
      const message = isApiError(err) && err.code === 40941
        ? t('settings.skills.versioning.protectedVersionDeleteBlocked')
        : (err instanceof Error ? err.message : t('messages.error'))
      toast.error(message)
    },
  })

  const clearVersionsMutation = useMutation({
    mutationFn: async () => clearWorkflowVersions(workflowId!),
    onSuccess: (payload) => {
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      if (payload.deletedCount > 0) {
        toast.success(t('settings.skills.versioning.clearSuccess', { count: payload.deletedCount }))
      } else {
        toast(t('settings.skills.versioning.clearNoop'))
      }
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('messages.error')
      toast.error(message)
    },
  })

  const handleSave = useCallback(() => {
    saveMutation.mutate()
  }, [saveMutation])

  const handlePublish = useCallback((versionName: string) => {
    publishMutation.mutate(versionName)
  }, [publishMutation])

  const handleDeleteVersion = useCallback((versionId: string) => {
    if (!window.confirm(t('settings.skills.versioning.deleteConfirm'))) return
    deleteVersionMutation.mutate(versionId)
  }, [deleteVersionMutation, t])

  const handleClearVersions = useCallback(() => {
    clearVersionsMutation.mutate()
  }, [clearVersionsMutation])

  const handleOpenVersionPanel = useCallback(() => {
    setVersionPanelOpen((prev) => !prev)
  }, [])

  const handleWorkflowEnvVarsChange = useCallback((nextVars: typeof workflowEnvVars) => {
    const startNode = getStartNodeFromNodes(store.nodes)
    if (!startNode) {
      toast.error(t('settings.skills.envVars.startNodeMissing'))
      return
    }
    const nextConfig = toStartConfigWithEnvVars(
      (startNode.data.config ?? null) as Record<string, unknown> | null,
      nextVars,
    )
    store.updateNodeConfig(startNode.id, nextConfig as NodeConfig, { pushHistory: true })
  }, [store, t, workflowEnvVars])

  const handleBack = useCallback(() => {
    if (hasUnsavedChanges && !window.confirm(t('settings.skills.unsavedChanges'))) return
    navigate('/settings/assistant-targets')
  }, [hasUnsavedChanges, navigate, t])

  const handleAutoLayout = useCallback(() => {
    if (store.nodes.length <= 1) return
    const laidOut = autoLayoutWorkflowWithSubflows(store.nodes, store.edges)
    store.pushHistory()
    store.setNodes(laidOut)
  }, [store])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
      if (mod && e.key === 'z' && !e.shiftKey) {
        e.preventDefault()
        store.undo()
      }
      if (mod && e.key === 'z' && e.shiftKey) {
        e.preventDefault()
        store.redo()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleSave, store])

  // Warn on page unload
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasUnsavedChanges])

  // Mutual exclusion: Close test run panel when property panel opens (node selected)
  useEffect(() => {
    if (store.selectedNodeId || store.selectedSubflowNodeId) {
      setTestRunPanelOpen(false)
    }
  }, [store.selectedNodeId, store.selectedSubflowNodeId, setTestRunPanelOpen])

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        <span>{t('messages.loading')}</span>
      </div>
    )
  }

  return (
    <ReactFlowProvider>
      <div className="relative w-screen h-screen bg-slate-50 overflow-hidden">
        {/* Full Screen Canvas */}
        <div className="absolute inset-0 z-0">
          <FlowCanvas
            tools={workflowTools}
            workflowDescription={workflowDescriptionDraft}
          />
        </div>

        {/* Floating Header */}
        <div className="absolute top-4 left-4 right-4 z-10 flex justify-between items-start pointer-events-none">
          {/* Left: Title & Back */}
          <div className="pointer-events-auto bg-white/70 backdrop-blur-md shadow-sm border border-white/50 rounded-2xl p-2 flex items-center gap-3 pr-4">
            <button
              onClick={handleBack}
              className="p-2 rounded-xl hover:bg-white/60 transition-colors"
              title={t('settings.skills.workflowActions.back')}
            >
              <ArrowLeft className="w-4 h-4 text-foreground/80" />
            </button>
            <div className="flex flex-col">
              <h1 className="text-sm font-semibold leading-none">{workflowEntity?.name ?? ''}</h1>
              <span className="text-[10px] text-muted-foreground mt-0.5">{t('settings.skills.workflowEditor')}</span>
            </div>
          </div>

          {/* Right: Actions */}
          <div className="pointer-events-auto bg-white/70 backdrop-blur-md shadow-sm border border-white/50 rounded-2xl p-2 flex items-center gap-1.5">
            <button
              onClick={() => store.undo()}
              disabled={!store.canUndo()}
              className="p-2 rounded-xl hover:bg-white/60 disabled:opacity-30 transition-colors"
              title={t('settings.skills.workflowActions.undo')}
            >
              <Undo2 className="w-4 h-4 text-foreground/80" />
            </button>
            <button
              onClick={() => store.redo()}
              disabled={!store.canRedo()}
              className="p-2 rounded-xl hover:bg-white/60 disabled:opacity-30 transition-colors"
              title={t('settings.skills.workflowActions.redo')}
            >
              <Redo2 className="w-4 h-4 text-foreground/80" />
            </button>
            <button
              onClick={handleAutoLayout}
              disabled={store.nodes.length <= 1}
              className="p-2 rounded-xl hover:bg-white/60 disabled:opacity-30 transition-colors"
              title={t('settings.skills.workflowActions.autoLayout')}
            >
              <LayoutTemplate className="w-4 h-4 text-foreground/80" />
            </button>
            <button
              onClick={() => {
                if (!testRunPanelOpen) {
                  store.setSelectedNodeId(null)
                  store.clearSelectedSubflowSelection()
                }
                setTestRunPanelOpen(!testRunPanelOpen)
              }}
              className={`
                flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm font-medium transition-all shadow-sm
                ${testRunPanelOpen
                  ? 'bg-blue-100 text-blue-700 hover:bg-blue-200 border border-blue-200'
                  : 'bg-white/90 hover:bg-white text-slate-700 border border-slate-200 hover:border-slate-300'
                }
              `}
              title={t('settings.skills.workflowActions.testRun')}
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              {t('settings.skills.workflowActions.testRun')}
            </button>
            <HoverCard openDelay={200} closeDelay={100}>
              <HoverCardTrigger asChild>
                <button
                  onClick={handleValidateNow}
                  className={`
                    relative flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm font-medium transition-all shadow-sm
                    ${validationPanelOpen
                      ? 'bg-blue-100 text-blue-700 hover:bg-blue-200 border border-blue-200'
                      : 'bg-white/90 hover:bg-white text-slate-700 border border-slate-200 hover:border-slate-300'
                    }
                  `}
                >
                  <ListChecks className={`w-3.5 h-3.5 ${isValidating ? 'animate-pulse text-blue-600' : ''}`} />
                  {t('settings.skills.workflowActions.validate')}
                  {(validationErrors.length > 0) && (
                    <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500"></span>
                    </span>
                  )}
                  {(!isValidating && lastValidatedAt && validationErrors.length === 0) && (
                    <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500"></span>
                    </span>
                  )}
                </button>
              </HoverCardTrigger>
              <HoverCardContent side="bottom" align="end" className="w-60 p-3">
                <div
                  className="flex flex-col gap-2 cursor-pointer"
                  onClick={() => setValidationPanelOpen(true)}
                >
                  <div className="flex items-center gap-2">
                    {isValidating ? (
                      <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
                    ) : validationErrors.length > 0 ? (
                      <AlertCircle className="w-4 h-4 text-red-600" />
                    ) : validationWarnings.length > 0 ? (
                      <AlertCircle className="w-4 h-4 text-amber-600" />
                    ) : lastValidatedAt ? (
                      <ListChecks className="w-4 h-4 text-green-600" />
                    ) : (
                      <ListChecks className="w-4 h-4 text-muted-foreground" />
                    )}
                    <span className={`text-sm font-medium ${isValidating ? 'text-blue-700' :
                      validationErrors.length > 0 ? 'text-red-700' :
                        validationWarnings.length > 0 ? 'text-amber-700' :
                          lastValidatedAt ? 'text-green-700' : 'text-foreground'
                      }`}>
                      {isValidating ? t('settings.skills.workflowValidationPopup.running') :
                        validationErrors.length > 0 ? t('settings.skills.workflowValidationPopup.failed') :
                          validationWarnings.length > 0 ? t('settings.skills.workflowValidationPopup.warnings') :
                            lastValidatedAt ? t('settings.skills.workflowValidationPopup.success') :
                              t('settings.skills.workflowValidationPopup.ready')}
                    </span>
                  </div>

                  {(validationErrors.length > 0 || validationWarnings.length > 0) && (
                    <div className="flex items-center gap-3 text-xs pl-6">
                      {validationErrors.length > 0 && (
                        <span className="text-red-600 font-medium">
                          {validationErrors.length} {t('settings.skills.workflowValidationPopup.errors')}
                        </span>
                      )}
                      {validationWarnings.length > 0 && (
                        <span className="text-amber-600 font-medium">
                          {validationWarnings.length} {t('settings.skills.workflowValidationPopup.warns')}
                        </span>
                      )}
                    </div>
                  )}

                  {lastValidatedAt && (
                    <div className="text-[10px] text-muted-foreground border-t pt-2 mt-1 flex justify-between items-center">
                      <span>{t('settings.skills.workflowValidationPopup.lastValidated', { time: new Date(lastValidatedAt).toLocaleTimeString() })}</span>
                      <ArrowLeft className="w-3 h-3 rotate-180 opacity-50" />
                    </div>
                  )}
                </div>
              </HoverCardContent>
            </HoverCard>
            <div className="w-px h-5 bg-border mx-1" />
            <button
              onClick={handleOpenVersionPanel}
              className={`
                flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm font-medium transition-all shadow-sm
                ${versionPanelOpen
                  ? 'bg-blue-100 text-blue-700 hover:bg-blue-200 border border-blue-200'
                  : 'bg-white/90 hover:bg-white text-slate-700 border border-slate-200 hover:border-slate-300'
                }
              `}
              title={t('settings.skills.workflowActions.versionHistory')}
            >
              <History className="w-3.5 h-3.5" />
              {t('settings.skills.workflowActions.versionHistory')}
            </button>
            <button
              onClick={() => setEnvPanelOpen((prev) => !prev)}
              className={`
                flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm font-medium transition-all shadow-sm
                ${envPanelOpen
                  ? 'bg-blue-100 text-blue-700 hover:bg-blue-200 border border-blue-200'
                  : 'bg-white/90 hover:bg-white text-slate-700 border border-slate-200 hover:border-slate-300'
                }
              `}
              title={t('settings.skills.workflowActions.env')}
            >
              <SlidersHorizontal className="w-3.5 h-3.5" />
              {t('settings.skills.workflowActions.env')}
            </button>
            <button
              onClick={() => setPublishDialogOpen(true)}
              disabled={publishMutation.isPending}
              className="flex items-center gap-2 px-4 py-1.5 rounded-xl text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 border border-blue-700 disabled:opacity-50 transition-all shadow-sm"
              title={t('settings.skills.workflowActions.saveAndPublish')}
            >
              {publishMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Send className="w-3.5 h-3.5" />
              )}
              {t('settings.skills.workflowActions.saveAndPublish')}
            </button>
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !hasUnsavedChanges}
              className={`
                flex items-center gap-2 px-4 py-1.5 rounded-xl text-sm font-medium transition-all shadow-sm
                ${hasUnsavedChanges
                  ? 'bg-primary text-primary-foreground hover:bg-primary/90 border border-primary'
                  : 'bg-white/50 text-muted-foreground hover:bg-white/60 border border-slate-200'
                }
              `}
            >
              {saveMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              {t('settings.skills.workflowActions.save')}
            </button>
          </div>
        </div>

        {/* Floating Palette (Left) */}
        <div className="absolute left-4 top-20 bottom-24 z-10 w-fit pointer-events-none flex flex-col justify-start min-h-0">
          <div className="pointer-events-auto min-h-0 flex flex-col">
            <NodePalette tools={workflowTools} />
          </div>
        </div>

        {/* Floating Property Panel (Right) */}
        <div className="absolute right-4 top-24 bottom-4 z-10 pointer-events-none flex flex-col items-end justify-start">
          <div className="pointer-events-auto h-full flex flex-col">
            <PropertyPanel
              tools={workflowTools}
              workflowDescription={workflowDescriptionDraft}
              onWorkflowDescriptionChange={setWorkflowDescriptionDraft}
            />
          </div>
        </div>

        <WorkflowValidationChecklistPanel
          open={validationPanelOpen}
          isValidating={isValidating}
          errors={validationErrors}
          warnings={validationWarnings}
          requestError={validationRequestError}
          lastValidatedAt={lastValidatedAt}
          onClose={() => setValidationPanelOpen(false)}
          onLocate={handleLocateValidationIssue}
          onRefresh={handleValidationRefresh}
        />

        {workflowId && <WorkflowTestRunPanel workflowId={workflowId} startInputMode={startInputMode} />}

        <TargetVersionPanel
          open={versionPanelOpen}
          loading={versionsLoading}
          loadError={versionsError instanceof Error ? versionsError.message : null}
          isSystemTarget={Boolean(workflowEntity?.isSystem)}
          draftVersionId={workflowVersions?.draftVersionId}
          publishedVersionId={workflowVersions?.publishedVersionId}
          versions={workflowVersions?.versions ?? []}
          clearing={clearVersionsMutation.isPending}
          deletingVersionId={deleteVersionMutation.isPending ? deleteVersionMutation.variables : null}
          restoringVersionId={rollbackMutation.isPending ? rollbackMutation.variables : null}
          onClose={() => setVersionPanelOpen(false)}
          onRefresh={() => { void refetchVersions() }}
          onClear={handleClearVersions}
          onDelete={handleDeleteVersion}
          onRestore={(versionId) => rollbackMutation.mutate(versionId)}
        />

        <WorkflowEnvVarPanel
          open={envPanelOpen}
          envVars={workflowEnvVars}
          onClose={() => setEnvPanelOpen(false)}
          onChange={handleWorkflowEnvVarsChange}
        />

        <PublishVersionDialog
          open={publishDialogOpen}
          defaultName={defaultPublishVersionName}
          submitting={publishMutation.isPending}
          onOpenChange={setPublishDialogOpen}
          onConfirm={handlePublish}
        />
      </div>
    </ReactFlowProvider>
  )
}
