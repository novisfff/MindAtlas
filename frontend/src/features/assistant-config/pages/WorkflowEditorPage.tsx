import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { ArrowLeft, Save, Undo2, Redo2, Loader2, LayoutTemplate } from 'lucide-react'
import { toast } from 'sonner'
import { getSkill } from '../api/skills'
import { saveWorkflow, validateWorkflow } from '../api/workflow'
import { getSystemToolDefinitions, getToolsWithParams } from '../api/tools'
import { useWorkflowEditorStore } from '../stores/workflow-editor-store'
import { FlowCanvas } from '../components/workflow/FlowCanvas'
import { NodePalette } from '../components/workflow/NodePalette'
import { PropertyPanel } from '../components/workflow/PropertyPanel'
import { serializeToWorkflowInput, deserializeFromSkill } from '../components/workflow/serialization'
import type { WorkflowToolDefinition } from '../components/workflow/types'
import { autoLayoutWorkflowWithSubflows } from '../components/workflow/autoLayout'

import '@xyflow/react/dist/style.css'

export default function WorkflowEditorPage() {
  const { skillId } = useParams<{ skillId: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const qc = useQueryClient()
  const initialized = useRef(false)

  const store = useWorkflowEditorStore()

  const { data: skill, isLoading } = useQuery({
    queryKey: ['assistant-skill', skillId],
    queryFn: () => getSkill(skillId!),
    enabled: !!skillId,
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

  // Load workflow data into store on first fetch
  useEffect(() => {
    if (skill && !initialized.current) {
      initialized.current = true
      const { nodes, edges, viewport } = deserializeFromSkill(skill)
      store.loadWorkflow(nodes, edges, viewport)
    }
  }, [skill])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const input = serializeToWorkflowInput(store.nodes, store.edges, store.viewport)
      const validation = await validateWorkflow(skillId!, input)
      if (!validation.valid) {
        const msg = validation.errors.map((e) => e.message).slice(0, 3).join('; ')
        throw new Error(msg || t('settings.skills.workflowValidationFailed'))
      }
      return saveWorkflow(skillId!, input)
    },
    onSuccess: () => {
      store.resetDirty()
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      toast.success(t('settings.skills.workflowSaved'))
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('settings.skills.workflowSaveError')
      toast.error(message)
    },
  })

  const handleSave = useCallback(() => {
    saveMutation.mutate()
  }, [saveMutation])

  const handleBack = useCallback(() => {
    if (store.isDirty && !window.confirm(t('settings.skills.unsavedChanges'))) return
    navigate('/settings/assistant-skills')
  }, [store.isDirty, navigate, t])

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
      if (store.isDirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [store.isDirty])

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <ReactFlowProvider>
      <div className="relative w-screen h-screen bg-slate-50 overflow-hidden">
        {/* Full Screen Canvas */}
        <div className="absolute inset-0 z-0">
          <FlowCanvas tools={workflowTools} />
        </div>

        {/* Floating Header */}
        <div className="absolute top-4 left-4 right-4 z-10 flex justify-between items-start pointer-events-none">
          {/* Left: Title & Back */}
          <div className="pointer-events-auto bg-white/90 backdrop-blur-sm shadow-sm border rounded-xl p-2 flex items-center gap-3 pr-4">
            <button
              onClick={handleBack}
              className="p-2 rounded-lg hover:bg-muted transition-colors"
              title={t('settings.skills.workflowActions.back')}
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <div className="flex flex-col">
              <h1 className="text-sm font-semibold leading-none">{skill?.name ?? ''}</h1>
              <span className="text-[10px] text-muted-foreground mt-0.5">{t('settings.skills.workflowEditor')}</span>
            </div>
          </div>

          {/* Right: Actions */}
          <div className="pointer-events-auto bg-white/90 backdrop-blur-sm shadow-sm border rounded-xl p-2 flex items-center gap-1">
            <button
              onClick={() => store.undo()}
              disabled={!store.canUndo()}
              className="p-2 rounded-lg hover:bg-muted disabled:opacity-30 transition-colors"
              title={t('settings.skills.workflowActions.undo')}
            >
              <Undo2 className="w-4 h-4" />
            </button>
            <button
              onClick={() => store.redo()}
              disabled={!store.canRedo()}
              className="p-2 rounded-lg hover:bg-muted disabled:opacity-30 transition-colors"
              title={t('settings.skills.workflowActions.redo')}
            >
              <Redo2 className="w-4 h-4" />
            </button>
            <button
              onClick={handleAutoLayout}
              disabled={store.nodes.length <= 1}
              className="p-2 rounded-lg hover:bg-muted disabled:opacity-30 transition-colors"
              title={t('settings.skills.workflowActions.autoLayout')}
            >
              <LayoutTemplate className="w-4 h-4" />
            </button>
            <div className="w-px h-4 bg-border mx-2" />
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !store.isDirty}
              className={`
                flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all
                ${store.isDirty
                  ? 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
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
        <div className="absolute left-4 top-24 bottom-4 z-10 w-fit pointer-events-none flex flex-col justify-center">
          <div className="pointer-events-auto">
            <NodePalette tools={workflowTools} />
          </div>
        </div>

        {/* Floating Property Panel (Right) */}
        <div className="absolute right-4 top-24 bottom-4 z-10 pointer-events-none flex flex-col items-end justify-start">
          <div className="pointer-events-auto h-full flex flex-col">
            <PropertyPanel tools={workflowTools} />
          </div>
        </div>
      </div>
    </ReactFlowProvider>
  )
}
