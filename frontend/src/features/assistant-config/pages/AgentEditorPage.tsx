import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Bot,
  BrainCircuit,
  Copy,
  Eraser,
  History,
  Play,
  Save,
  Send,
  Sparkles,
  Square,
  Wrench,
  Zap,
  Plus,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { isApiError } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { ToolCallDisplay } from '@/features/assistant/components/ToolCallDisplay'
import {
  clearAgentVersions,
  deleteAgentVersion,
  getAgentProfile,
  listAgentVersions,
  publishAgent,
  rollbackAgentVersion,
  runAgentTestStream,
  type AgentTestRunEvent,
} from '../api/agents'
import {
  useCopyAgentProfileMutation,
  useSystemToolDefinitionsQuery,
  useToolsQuery,
  useUpdateAgentProfileMutation,
} from '../queries'
import { useAgentTestRunStore } from '../stores/agent-test-run-store'
import { useModelsQuery } from '../../ai-providers/queries'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { PublishVersionDialog } from '../components/versioning/PublishVersionDialog'
import { TargetVersionPanel } from '../components/versioning/TargetVersionPanel'

interface ToolOption {
  name: string
  displayName: string
  description?: string
  enabled: boolean
}

const DEFAULT_MODEL_VALUE = '__system_default_model__'

function buildCompletedConversationHistory(
  messages: Array<{
    role: 'user' | 'assistant'
    content: string
    status?: 'running' | 'completed' | 'error' | 'cancelled'
  }>,
): Array<{ role: 'user' | 'assistant'; content: string }> {
  const history: Array<{ role: 'user' | 'assistant'; content: string }> = []
  let pendingUser: { role: 'user'; content: string } | null = null

  for (const message of messages) {
    const content = message.content.trim()
    if (!content) continue

    if (message.role === 'user') {
      pendingUser = { role: 'user', content }
      continue
    }

    if (message.status !== 'completed') {
      pendingUser = null
      continue
    }

    if (pendingUser) {
      history.push(pendingUser)
      pendingUser = null
    }

    history.push({ role: 'assistant', content })
  }

  return history
}

export default function AgentEditorPage() {
  const { agentProfileId } = useParams<{ agentProfileId: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const qc = useQueryClient()
  const updateMutation = useUpdateAgentProfileMutation()
  const copyAgentMutation = useCopyAgentProfileMutation()

  const [initializedForId, setInitializedForId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [kbEnabled, setKbEnabled] = useState(false)
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const [modelSource, setModelSource] = useState<'default' | 'custom'>('default')
  const [modelId, setModelId] = useState('')
  const [versionPanelOpen, setVersionPanelOpen] = useState(false)
  const [publishDialogOpen, setPublishDialogOpen] = useState(false)
  const runSubmitLockedRef = useRef(false)

  const { data: agent, isLoading } = useQuery({
    queryKey: ['assistant-agent-profile', agentProfileId],
    queryFn: () => getAgentProfile(agentProfileId!),
    enabled: !!agentProfileId,
  })
  const {
    data: agentVersions,
    isFetching: versionsLoading,
    error: versionsError,
    refetch: refetchVersions,
  } = useQuery({
    queryKey: ['assistant-agent-versions', agentProfileId],
    queryFn: () => listAgentVersions(agentProfileId!),
    enabled: !!agentProfileId && versionPanelOpen,
  })
  const { data: systemToolDefs = [] } = useSystemToolDefinitionsQuery()
  const { data: customTools = [] } = useToolsQuery()
  const { data: llmModels = [] } = useModelsQuery({ modelType: 'llm' })

  const {
    status,
    input,
    streamOutput,
    result,
    messages,
    setInput,
    setStreamOutput,
    beginRun,
    cancelRun,
    ingestEvent,
    markRunError,
    clearResult,
  } = useAgentTestRunStore()

  useEffect(() => {
    if (!agent || !agentProfileId) return
    if (initializedForId === agentProfileId) return
    setInitializedForId(agentProfileId)
    setName(agent.name || '')
    setDescription(agent.description || '')
    setSystemPrompt(agent.systemPrompt || '')
    setKbEnabled(Boolean(agent.kbConfig?.enabled))
    setSelectedTools(Array.isArray(agent.tools) ? agent.tools.map((item) => String(item)) : [])
    const incomingModelSource = String(agent.modelSource ?? 'default') === 'custom' ? 'custom' : 'default'
    setModelSource(incomingModelSource)
    setModelId(incomingModelSource === 'custom' && agent.modelId ? String(agent.modelId) : '')
  }, [agent, agentProfileId, initializedForId])

  const validTools = useMemo(() => {
    const all: ToolOption[] = []
    systemToolDefs.forEach((def) => {
      all.push({
        name: def.name,
        displayName: def.displayName || def.name,
        description: def.displayDescription || def.description || undefined,
        enabled: selectedTools.includes(def.name),
      })
    })
    customTools.forEach((ct) => {
      all.push({
        name: ct.name,
        displayName: ct.name,
        description: ct.description || undefined,
        enabled: selectedTools.includes(ct.name),
      })
    })
    return all
  }, [systemToolDefs, customTools, selectedTools])

  const hasRunContent = useMemo(
    () => messages.length > 0 || status !== 'idle' || Boolean(result.errorMessage),
    [messages.length, result.errorMessage, status],
  )

  const modelSelectValue =
    modelSource === 'custom' && modelId ? modelId : DEFAULT_MODEL_VALUE
  const defaultPublishVersionName = useMemo(
    () => new Date().toLocaleString(),
    [publishDialogOpen],
  )
  const modelInList = llmModels.some((item) => item.id === modelId)
  const isSystemAgent = Boolean(agent?.isSystem)
  const modelOptions = [
    { label: t('settings.skills.agentModelDefault'), value: DEFAULT_MODEL_VALUE },
    ...llmModels.map((item) => ({ label: item.name, value: item.id })),
    ...(modelSource === 'custom' && modelId && !modelInList ? [{ label: modelId, value: modelId }] : []),
  ]

  const toggleTool = (toolName: string) => {
    if (isSystemAgent) return
    if (selectedTools.includes(toolName)) {
      setSelectedTools(selectedTools.filter((t) => t !== toolName))
    } else {
      setSelectedTools([...selectedTools, toolName])
    }
  }

  const handleSave = () => {
    if (isSystemAgent) return
    if (!agentProfileId) return
    if (!name.trim()) {
      toast.error(t('settings.skills.nameRequired'))
      return
    }
    if (!systemPrompt.trim()) {
      toast.error(t('settings.skills.systemPromptRequired'))
      return
    }
    if (modelSource === 'custom' && !modelId) {
      toast.error(t('settings.skills.agentModelRequired'))
      return
    }

    updateMutation.mutate(
      {
        id: agentProfileId,
        data: {
          name,
          description,
          systemPrompt,
          kbConfig: { enabled: kbEnabled },
          tools: selectedTools,
          modelSource,
          modelId: modelSource === 'custom' ? modelId : null,
        },
      },
      {
        onSuccess: () => {
          qc.invalidateQueries({ queryKey: ['assistant-agent-profile', agentProfileId] })
          toast.success(t('messages.saved'))
        },
        onError: () => {
          toast.error(t('messages.error'))
        },
      }
    )
  }

  const handleCopyAgent = async () => {
    if (!agentProfileId) return
    const loadingToastId = toast.loading(t('settings.skills.agentCopying'))
    try {
      const copied = await copyAgentMutation.mutateAsync(agentProfileId)
      toast.success(t('settings.skills.agentCopied'), { id: loadingToastId })
      navigate(`/settings/agent-editor/${copied.id}`)
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message, { id: loadingToastId })
    }
  }

  const publishMutation = useMutation({
    mutationFn: async (versionName: string) => publishAgent(agentProfileId!, {
      draft: {
        systemPrompt,
        tools: selectedTools,
        kbConfig: { enabled: kbEnabled },
        modelSource,
        modelId: modelSource === 'custom' ? modelId : null,
      },
      versionName: versionName.trim() || undefined,
    }),
    onSuccess: () => {
      setPublishDialogOpen(false)
      qc.invalidateQueries({ queryKey: ['assistant-agent-profile', agentProfileId] })
      qc.invalidateQueries({ queryKey: ['assistant-agent-versions', agentProfileId] })
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
      toast.success(t('settings.skills.versioning.publishSuccess'))
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    },
  })

  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => rollbackAgentVersion(agentProfileId!, versionId),
    onSuccess: (payload) => {
      if (payload.agentDraft) {
        setSystemPrompt(payload.agentDraft.systemPrompt || '')
        setSelectedTools(Array.isArray(payload.agentDraft.tools) ? payload.agentDraft.tools.map((item) => String(item)) : [])
        setKbEnabled(Boolean(payload.agentDraft.kbConfig?.enabled))
        const incomingSource = payload.agentDraft.modelSource === 'custom' ? 'custom' : 'default'
        setModelSource(incomingSource)
        setModelId(incomingSource === 'custom' && payload.agentDraft.modelId ? String(payload.agentDraft.modelId) : '')
      }
      qc.invalidateQueries({ queryKey: ['assistant-agent-profile', agentProfileId] })
      qc.invalidateQueries({ queryKey: ['assistant-agent-versions', agentProfileId] })
      toast.success(t('settings.skills.versioning.restoreSuccess'))
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    },
  })

  const deleteVersionMutation = useMutation({
    mutationFn: async (versionId: string) => deleteAgentVersion(agentProfileId!, versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-agent-profile', agentProfileId] })
      qc.invalidateQueries({ queryKey: ['assistant-agent-versions', agentProfileId] })
      toast.success(t('settings.skills.versioning.deleteSuccess'))
    },
    onError: (error) => {
      const message = isApiError(error) && error.code === 40944
        ? t('settings.skills.versioning.protectedVersionDeleteBlocked')
        : (error instanceof Error ? error.message : t('messages.error'))
      toast.error(message)
    },
  })

  const clearVersionsMutation = useMutation({
    mutationFn: async () => clearAgentVersions(agentProfileId!),
    onSuccess: (payload) => {
      qc.invalidateQueries({ queryKey: ['assistant-agent-profile', agentProfileId] })
      qc.invalidateQueries({ queryKey: ['assistant-agent-versions', agentProfileId] })
      if (payload.deletedCount > 0) {
        toast.success(t('settings.skills.versioning.clearSuccess', { count: payload.deletedCount }))
      } else {
        toast(t('settings.skills.versioning.clearNoop'))
      }
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    },
  })

  const handleDeleteVersion = (versionId: string) => {
    if (!window.confirm(t('settings.skills.versioning.deleteConfirm'))) return
    deleteVersionMutation.mutate(versionId)
  }

  const handleClearVersions = () => {
    clearVersionsMutation.mutate()
  }

  const handleRunTest = async () => {
    if (runSubmitLockedRef.current || status === 'running') return
    runSubmitLockedRef.current = true

    try {
      if (!input.trim() || !systemPrompt.trim()) return
      if (modelSource === 'custom' && !modelId) {
        toast.error(t('settings.skills.agentModelRequired'))
        return
      }
      const submittedInput = input.trim()
      const history = buildCompletedConversationHistory(messages)
      const ctrl = new AbortController()
      beginRun(ctrl, submittedInput)

      await runAgentTestStream(
        agentProfileId!,
        {
          draft: {
            systemPrompt,
            tools: selectedTools,
            kbConfig: { enabled: kbEnabled },
            modelSource,
            modelId: modelSource === 'custom' ? modelId : null,
          },
          userInput: submittedInput,
          history,
          streamOutput,
        },
        {
          signal: ctrl.signal,
          onEvent: (event: AgentTestRunEvent) => {
            ingestEvent(event)
          },
        },
      )
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return
      }
      console.error(err)
      markRunError(err instanceof Error ? err.message : String(err))
      toast.error(t('settings.skills.agentTestRunFailed'))
    } finally {
      runSubmitLockedRef.current = false
    }
  }

  if (isLoading || !agent) {
    return (
      <div className="flex items-center justify-center py-8 gap-2 text-sm text-muted-foreground">
        <Sparkles className="w-6 h-6 animate-spin text-muted-foreground" />
        <span>{t('messages.loading')}</span>
      </div>
    )
  }

  return (
    <div className="flex h-screen bg-background text-foreground">
      {/* LEFT PANEL: CONFIGURATION */}
      <div className="flex-1 flex flex-col min-w-0 border-r">
        {/* Header */}
        <div className="flex flex-col gap-4 border-b px-6 py-4 bg-background z-10">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button onClick={() => navigate('/settings/assistant-targets')} className="p-2 -ml-2 rounded-lg hover:bg-muted transition-colors">
                <ArrowLeft className="w-5 h-5 text-muted-foreground" />
              </button>
              <div>
                <h1 className="text-lg font-semibold tracking-tight">{t('settings.skills.agentEditorTitle')}</h1>
                <p className="text-xs text-muted-foreground font-mono">{agent.id}</p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => navigate('/settings/assistant-targets')}
                className="px-4 py-2 text-sm rounded-lg border hover:bg-muted transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={() => setVersionPanelOpen((prev) => !prev)}
                className={`
                  px-4 py-2 text-sm rounded-lg border transition-colors inline-flex items-center gap-2
                  ${versionPanelOpen ? 'bg-blue-100 text-blue-700 border-blue-200' : 'hover:bg-muted'}
                `}
              >
                <History className="w-4 h-4" />
                {t('settings.skills.workflowActions.versionHistory')}
              </button>
              <button
                onClick={() => void handleCopyAgent()}
                disabled={copyAgentMutation.isPending}
                className="px-4 py-2 text-sm rounded-lg border hover:bg-muted transition-colors inline-flex items-center gap-2 disabled:opacity-50"
              >
                {copyAgentMutation.isPending ? (
                  <Sparkles className="w-4 h-4 animate-spin" />
                ) : (
                  <Copy className="w-4 h-4" />
                )}
                {t('settings.skills.copyAsDuplicate')}
              </button>
              <button
                onClick={() => {
                  if (isSystemAgent) return
                  if (!systemPrompt.trim()) {
                    toast.error(t('settings.skills.systemPromptRequired'))
                    return
                  }
                  if (modelSource === 'custom' && !modelId) {
                    toast.error(t('settings.skills.agentModelRequired'))
                    return
                  }
                  setPublishDialogOpen(true)
                }}
                disabled={publishMutation.isPending || isSystemAgent}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 inline-flex items-center gap-2 transition-all shadow-sm"
              >
                {publishMutation.isPending ? (
                  <Sparkles className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                {t('settings.skills.workflowActions.saveAndPublish')}
              </button>
              <button
                onClick={handleSave}
                disabled={updateMutation.isPending || !name.trim() || !systemPrompt.trim() || isSystemAgent}
                className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-2 transition-all shadow-sm"
              >
                {updateMutation.isPending ? (
                  <Sparkles className="w-4 h-4 animate-spin" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                {t('common.save')}
              </button>
            </div>
          </div>

          {isSystemAgent && (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="space-y-1">
                  <p className="text-sm font-semibold text-amber-900">
                    {t('settings.skills.systemTargetReadonlyBannerTitle')}
                  </p>
                  <p className="text-sm leading-6 text-amber-800">
                    {t('settings.skills.systemAgentReadonlyDescription')}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void handleCopyAgent()}
                  disabled={copyAgentMutation.isPending}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-900 transition-colors hover:bg-amber-100 disabled:opacity-50"
                >
                  {copyAgentMutation.isPending ? (
                    <Sparkles className="w-4 h-4 animate-spin" />
                  ) : (
                    <Copy className="w-4 h-4" />
                  )}
                  {t('settings.skills.copyAsDuplicate')}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Scrollable Content */}
        <ScrollArea className="flex-1">
          <div className="p-6 space-y-6 max-w-4xl mx-auto">
            {/* Identity Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-3 pb-1">
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                  <Bot className="w-4 h-4" />
                </div>
                <h2 className="text-base font-semibold tracking-tight text-foreground">{t('settings.skills.targetBasicInfo')}</h2>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-card hover:shadow-md transition-shadow duration-300 rounded-2xl border border-border/60 p-5 shadow-sm">
                <div className="space-y-2">
                  <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                    {t('settings.skills.name')} <span className="text-red-500">*</span>
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={isSystemAgent}
                    className="flex h-11 w-full rounded-xl border border-input/60 bg-background hover:bg-muted/10 px-4 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/20 focus-visible:border-blue-500/50 disabled:cursor-not-allowed disabled:opacity-50 transition-all"
                    placeholder={t('settings.skills.name')}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                    {t('settings.skills.description')}
                  </label>
                  <input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    disabled={isSystemAgent}
                    className="flex h-11 w-full rounded-xl border border-input/60 bg-background hover:bg-muted/10 px-4 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/20 focus-visible:border-blue-500/50 disabled:cursor-not-allowed disabled:opacity-50 transition-all"
                    placeholder={t('settings.skills.description')}
                  />
                </div>
              </div>
            </div>

            {/* System Prompt Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-3 pb-1">
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400">
                  <BrainCircuit className="w-4 h-4" />
                </div>
                <h2 className="text-base font-semibold tracking-tight text-foreground">{t('settings.skills.systemPrompt')}</h2>
              </div>

              <div className="bg-card hover:shadow-md transition-shadow duration-300 rounded-2xl border border-border/60 p-5 shadow-sm space-y-4">
                <div className="flex justify-between items-center">
                  <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                    {t('settings.skills.systemPrompt')} <span className="text-red-500">*</span>
                  </label>
                  <span className="text-xs text-muted-foreground bg-muted/50 px-2 py-1 rounded-md">{t('settings.skills.systemPromptHelpText')}</span>
                </div>
                <textarea
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  disabled={isSystemAgent}
                  className="flex min-h-[320px] w-full rounded-xl border border-input/60 bg-muted/10 hover:bg-muted/20 focus:bg-background px-4 py-3 text-sm font-mono leading-relaxed ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/20 focus-visible:border-purple-500/50 disabled:cursor-not-allowed disabled:opacity-50 resize-y transition-all duration-200"
                  placeholder={t('settings.skills.systemPromptPlaceholder')}
                />
              </div>
            </div>

            {/* Capabilities Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-3 pb-1">
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                  <Wrench className="w-4 h-4" />
                </div>
                <h2 className="text-base font-semibold tracking-tight text-foreground">{t('settings.skills.targetRuntimeConfig')}</h2>
              </div>

              <div className="bg-card hover:shadow-md transition-shadow duration-300 rounded-2xl border border-border/60 p-5 shadow-sm space-y-6">
                {/* Model Selection */}
                <div className="space-y-2">
                  <label className="text-sm font-medium leading-none">{t('settings.skills.agentModel')}</label>
                  <select
                    value={modelSelectValue}
                    disabled={isSystemAgent}
                    onChange={(e) => {
                      const val = e.target.value
                      if (val === DEFAULT_MODEL_VALUE) {
                        setModelSource('default')
                        setModelId('')
                        return
                      }
                      setModelSource('custom')
                      setModelId(val)
                    }}
                    className="flex h-11 w-full rounded-xl border border-input/60 bg-background hover:bg-muted/10 px-4 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/20 focus-visible:border-emerald-500/50 transition-all cursor-pointer"
                  >
                    {modelOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  {llmModels.length === 0 && (
                    <p className="text-xs text-muted-foreground">{t('settings.skills.agentModelNoModels')}</p>
                  )}
                </div>

                {/* Knowledge Base */}
                <div className="flex items-center justify-between p-5 rounded-xl border border-border/60 bg-muted/10 hover:bg-muted/30 transition-colors">
                  <div className="space-y-1.5">
                    <label className="text-sm font-semibold leading-none">{t('settings.skills.kbEnabled')}</label>
                    <p className="text-xs text-muted-foreground">{t('settings.skills.kbEnabledDesc')}</p>
                  </div>
                  <Switch checked={kbEnabled} onCheckedChange={setKbEnabled} disabled={isSystemAgent} className="data-[state=checked]:bg-emerald-500" />
                </div>

                {/* Tools */}
                <div className="space-y-3">
                  <label className="text-sm font-medium leading-none">{t('settings.skills.agentTools')}</label>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {validTools.filter(t => t.enabled).map((tool) => (
                      <HoverCard key={tool.name} openDelay={200} closeDelay={100}>
                        <HoverCardTrigger asChild>
                          <div
                            onClick={() => toggleTool(tool.name)}
                            className={cn(
                              "group flex items-center justify-between p-3 rounded-xl border bg-emerald-500/5 border-emerald-500/40 ring-1 ring-emerald-500/10 shadow-sm transition-all duration-200",
                              isSystemAgent ? "cursor-not-allowed opacity-80" : "cursor-pointer hover:bg-emerald-500/10",
                            )}
                            title={t('common.remove', { defaultValue: 'Remove' })}
                          >
                            <div className="flex items-center gap-3 w-full">
                              <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                                <Zap className="w-4 h-4" />
                              </div>
                              <span className="text-sm font-medium text-emerald-700 dark:text-emerald-400 truncate">
                                {tool.displayName}
                              </span>
                            </div>
                            {!isSystemAgent ? (
                              <div className="opacity-0 group-hover:opacity-100 p-1 text-emerald-600/60 hover:text-emerald-700 transition-all">
                                <X className="w-3.5 h-3.5" />
                              </div>
                            ) : null}
                          </div>
                        </HoverCardTrigger>
                        <HoverCardContent side="top" align="start" className="w-80 p-4 space-y-2 shadow-lg z-50">
                          <div className="flex items-center gap-2">
                            <div className="p-1.5 rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                              <Zap className="w-4 h-4" />
                            </div>
                            <div className="min-w-0">
                              <h4 className="truncate text-sm font-semibold">{tool.displayName}</h4>
                              {tool.displayName !== tool.name ? (
                                <code className="block truncate text-[11px] text-muted-foreground">{tool.name}</code>
                              ) : null}
                            </div>
                          </div>
                          <p className="text-sm text-muted-foreground leading-relaxed">
                            {tool.description || t('settings.skills.noToolDescription', { defaultValue: 'No description available for this tool.' })}
                          </p>
                        </HoverCardContent>
                      </HoverCard>
                    ))}

                    <Popover>
                      <PopoverTrigger asChild>
                        <button disabled={isSystemAgent} className="group flex items-center justify-center gap-2 p-3 h-full min-h-[58px] rounded-xl border border-dashed border-border/60 bg-muted/10 hover:bg-muted/30 hover:border-primary/50 transition-all text-muted-foreground hover:text-foreground shadow-sm disabled:cursor-not-allowed disabled:opacity-50">
                          <Plus className="w-4 h-4" />
                          <span className="text-sm font-medium">{t('settings.skills.addTool', { defaultValue: 'Add Tool' })}</span>
                        </button>
                      </PopoverTrigger>
                      <PopoverContent className="w-[300px] p-2" align="start">
                        <div className="space-y-1">
                          <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase px-2 py-1.5">{t('settings.skills.availableTools', { defaultValue: 'Available Tools' })}</h4>
                          <ScrollArea className="h-[220px]">
                            {validTools.filter(t => !t.enabled).length === 0 ? (
                              <div className="flex flex-col items-center justify-center text-center py-8 text-sm text-muted-foreground">
                                <Zap className="w-8 h-8 opacity-20 mb-2" />
                                {t('settings.skills.noAgentOptions')}
                              </div>
                            ) : (
                              <div className="space-y-1 pr-3">
                                {validTools.filter(t => !t.enabled).map((tool) => (
                                  <HoverCard key={tool.name} openDelay={300} closeDelay={100}>
                                    <HoverCardTrigger asChild>
                                      <button
                                        onClick={() => toggleTool(tool.name)}
                                        disabled={isSystemAgent}
                                        className="w-full flex items-center gap-3 px-3 py-2 text-sm rounded-lg hover:bg-muted transition-colors text-left group"
                                      >
                                        <div className="p-1.5 rounded-md bg-muted group-hover:bg-primary/10 transition-colors">
                                          <Zap className="w-3.5 h-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
                                        </div>
                                        <span className="font-medium truncate">{tool.displayName}</span>
                                      </button>
                                    </HoverCardTrigger>
                                    <HoverCardContent side="right" align="start" className="w-72 p-3 space-y-1.5 shadow-lg z-50">
                                      <div className="min-w-0">
                                        <h4 className="truncate text-sm font-semibold">{tool.displayName}</h4>
                                        {tool.displayName !== tool.name ? (
                                          <code className="block truncate text-[11px] text-muted-foreground">{tool.name}</code>
                                        ) : null}
                                      </div>
                                      <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
                                        {tool.description || t('settings.skills.noToolDescription', { defaultValue: 'No description available for this tool.' })}
                                      </p>
                                    </HoverCardContent>
                                  </HoverCard>
                                ))}
                              </div>
                            )}
                          </ScrollArea>
                        </div>
                      </PopoverContent>
                    </Popover>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </ScrollArea>
      </div>

      {/* RIGHT PANEL: TEST PLAYGROUND */}
      <div className="w-[400px] xl:w-[480px] flex flex-col border-l bg-slate-50/50 dark:bg-zinc-950/50 shrink-0">
        <div className="flex items-center justify-between px-5 py-4 border-b bg-background/70 backdrop-blur-md sticky top-0 z-10">
          <div className="flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-md bg-purple-500/10 text-purple-600 dark:text-purple-400">
              <Sparkles className="w-4 h-4" />
            </div>
            <span className="font-semibold text-sm tracking-tight">{t('settings.skills.agentTestTitle')}</span>
          </div>
          <button
            onClick={clearResult}
            className="p-1.5 text-xs text-muted-foreground hover:text-red-500 hover:bg-red-500/10 rounded-md transition-colors"
            title={t('settings.skills.agentTestClear')}
          >
            <Eraser className="w-4 h-4" />
          </button>
        </div>

        <ScrollArea className="flex-1 p-4">
          {!hasRunContent ? (
            <div className="h-full flex flex-col items-center justify-center text-center space-y-6 min-h-[300px] opacity-80 select-none">
              <div className="relative">
                <div className="absolute inset-0 bg-purple-500/10 blur-xl rounded-full" />
                <div className="relative p-7 bg-background rounded-full border border-dashed border-border/80 shadow-sm">
                  <Bot className="w-8 h-8 text-muted-foreground/40" />
                </div>
              </div>
              <div className="space-y-1.5">
                <p className="text-sm font-medium text-foreground">{t('settings.skills.agentTestResultEmpty')}</p>
                <p className="text-xs text-muted-foreground">{t('settings.skills.agentTestDesc')}</p>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((message) => {
                if (message.role === 'user') {
                  return (
                    <div key={message.id} className="flex justify-end">
                      <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-[4px] px-4 py-2.5 max-w-[85%] text-sm shadow-sm leading-relaxed whitespace-pre-wrap">
                        {message.content}
                      </div>
                    </div>
                  )
                }

                const toolCallDisplayItems = (message.toolCalls ?? []).map((toolCall) => ({
                  id: toolCall.id,
                  name: toolCall.name,
                  args: toolCall.args || {},
                  result: toolCall.result,
                  status: toolCall.status,
                  toolKind: toolCall.toolKind,
                  agentRound: toolCall.agentRound,
                  toolCallIndex: toolCall.toolCallIndex,
                  startedAt: toolCall.startedAt,
                  endedAt: toolCall.endedAt,
                  durationMs: toolCall.durationMs ?? undefined,
                }))

                return (
                  <div key={message.id} className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-purple-100 to-blue-100 dark:from-purple-900/50 dark:to-blue-900/50 flex items-center justify-center shrink-0 border border-purple-200/50 dark:border-purple-800/50 shadow-sm">
                      <Sparkles className="w-4 h-4 text-purple-600 dark:text-purple-400" />
                    </div>
                    <div className="flex-1 space-y-1.5">
                      <div className="text-xs font-medium text-muted-foreground ml-1">Assistant</div>
                      {toolCallDisplayItems.length > 0 && (
                        <div className="space-y-2 pb-1">
                          <div className="flex items-center gap-2 ml-1">
                            <Badge variant="secondary" className="text-[10px] font-medium">
                              Tool Chain
                            </Badge>
                            <span className="text-[11px] text-muted-foreground">
                              {toolCallDisplayItems.length} step{toolCallDisplayItems.length > 1 ? 's' : ''}
                            </span>
                          </div>
                          <ToolCallDisplay toolCalls={toolCallDisplayItems} variant="compact" />
                        </div>
                      )}
                      <div className="bg-background border border-border/50 rounded-2xl rounded-tl-[4px] p-4 shadow-sm text-sm prose prose-sm dark:prose-invert max-w-none leading-relaxed">
                        {message.status === 'running' && !message.content ? (
                          <span className="animate-pulse">Thinking...</span>
                        ) : (
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {message.content || (message.status === 'error' ? result.errorMessage || '' : '')}
                          </ReactMarkdown>
                        )}
                      </div>
                      {message.status === 'running' && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground animate-pulse">
                          <span className="w-2 h-2 bg-green-500 rounded-full" />
                          Generating...
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </ScrollArea>

        {/* Input Area */}
        <div className="p-4 border-t border-border/40 bg-background/80 backdrop-blur-md z-10">
          <div className="relative group">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing || e.repeat) return
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  if (status !== 'running' && !runSubmitLockedRef.current) {
                    void handleRunTest()
                  }
                }
              }}
              disabled={status === 'running'}
              className="w-full min-h-[80px] max-h-[200px] p-4 pr-14 rounded-2xl border border-input/60 bg-background hover:bg-muted/10 focus:bg-background focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all resize-none shadow-sm text-sm"
              placeholder={t('settings.skills.agentTestInput')}
            />
            <div className="absolute bottom-3 right-3 flex items-center justify-center">
              {status === 'running' ? (
                <button
                  onClick={cancelRun}
                  className="p-2.5 rounded-xl bg-red-500 hover:bg-red-600 hover:scale-105 active:scale-95 text-white shadow-sm transition-all"
                >
                  <Square className="w-4 h-4 fill-current" />
                </button>
              ) : (
                <button
                  onClick={() => void handleRunTest()}
                  disabled={!input.trim() || !systemPrompt.trim()}
                  className="p-2.5 rounded-xl bg-primary hover:bg-primary/90 hover:scale-105 active:scale-95 text-primary-foreground shadow-sm disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 transition-all"
                >
                  <Play className="w-4 h-4 fill-current ml-0.5" />
                </button>
              )}
            </div>
          </div>
          <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground px-1">
            <span>Press <kbd className="bg-muted px-1 py-0.5 rounded border text-[10px]">Enter</kbd> to run</span>
            <label className="inline-flex items-center gap-2">
              <span>{t('settings.skills.agentTestStreamOutput')}</span>
              <Switch checked={streamOutput} onCheckedChange={setStreamOutput} />
            </label>
          </div>
        </div>
      </div>

      <TargetVersionPanel
        open={versionPanelOpen}
        loading={versionsLoading}
        loadError={versionsError instanceof Error ? versionsError.message : null}
        isSystemTarget={Boolean(agent?.isSystem)}
        draftVersionId={agentVersions?.draftVersionId}
        publishedVersionId={agentVersions?.publishedVersionId}
        versions={agentVersions?.versions ?? []}
        clearing={clearVersionsMutation.isPending}
        deletingVersionId={deleteVersionMutation.isPending ? deleteVersionMutation.variables : null}
        restoringVersionId={rollbackMutation.isPending ? rollbackMutation.variables : null}
        onClose={() => setVersionPanelOpen(false)}
        onRefresh={() => { void refetchVersions() }}
        onClear={handleClearVersions}
        onDelete={handleDeleteVersion}
        onRestore={(versionId) => rollbackMutation.mutate(versionId)}
      />

      <PublishVersionDialog
        open={publishDialogOpen}
        defaultName={defaultPublishVersionName}
        submitting={publishMutation.isPending}
        onOpenChange={setPublishDialogOpen}
        onConfirm={(versionName) => publishMutation.mutate(versionName)}
      />
    </div>
  )
}
