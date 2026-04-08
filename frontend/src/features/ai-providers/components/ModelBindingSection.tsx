import { useState } from 'react'
import { Bot, BrainCircuit, ChevronDown, ChevronRight, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ModelSelector } from './ModelSelector'
import { useModelBindingsQuery, useUpdateBindingsMutation, useModelsQuery } from '../queries'
import { toast } from 'sonner'
import { uiChrome } from '@/components/ui/styles'
import { SettingsBadge, SettingsInset } from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'

interface ModelBindingSectionProps {
  className?: string
}

export function ModelBindingSection({ className }: ModelBindingSectionProps) {
  const { t } = useTranslation()
  const [isExpanded, setIsExpanded] = useState(false)
  const { data: bindings, isLoading } = useModelBindingsQuery()
  const { data: allModels = [] } = useModelsQuery()
  const updateBindingsMutation = useUpdateBindingsMutation()

  const getModelName = (id?: string | null) => {
    if (!id) return t('settings.ai.notSet')
    return allModels.find(m => m.id === id)?.name || t('settings.ai.unknownModel')
  }

  const handleAssistantLlmChange = (modelId: string | null) => {
    updateBindingsMutation.mutate(
      { assistant: { llmModelId: modelId } },
      {
        onSuccess: () => toast.success(t('messages.success')),
        onError: () => toast.error(t('messages.error')),
      }
    )
  }

  const handleLightragLlmChange = (modelId: string | null) => {
    updateBindingsMutation.mutate(
      { lightrag: { llmModelId: modelId } },
      {
        onSuccess: () => toast.success(t('messages.success')),
        onError: () => toast.error(t('messages.error')),
      }
    )
  }

  const handleWorkflowCopilotLlmChange = (modelId: string | null) => {
    updateBindingsMutation.mutate(
      { workflowCopilot: { llmModelId: modelId } },
      {
        onSuccess: () => toast.success(t('messages.success')),
        onError: () => toast.error(t('messages.error')),
      }
    )
  }

  if (isLoading) {
    return (
      <div className={cn(uiChrome.card, "p-4", className)}>
        <div className="h-6 w-48 bg-muted animate-pulse rounded" />
      </div>
    )
  }

  return (
    <div className={cn(uiChrome.card, "overflow-hidden transition-all", className)}>
      <div
        className="flex cursor-pointer items-center justify-between gap-4 px-5 py-4 transition-colors hover:bg-muted/25"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-4 min-w-0 flex-1">
          <h3 className="shrink-0 text-sm font-semibold text-foreground">{t('settings.ai.sections.defaultBindings')}</h3>

          {!isExpanded && bindings && (
            <div className="flex items-center gap-2 truncate text-xs text-muted-foreground opacity-90">
              <div className="flex items-center gap-1">
                <Bot className="w-3 h-3" />
                <span>{getModelName(bindings.assistant?.llmModelId)}</span>
              </div>
              <span className="text-border">/</span>
              <div className="flex items-center gap-1">
                <BrainCircuit className="w-3 h-3" />
                <span>{getModelName(bindings.lightrag?.llmModelId)}</span>
              </div>
              <span className="text-border">/</span>
              <div className="flex items-center gap-1">
                <Sparkles className="w-3 h-3" />
                <span>{getModelName(bindings.workflowCopilot?.llmModelId)}</span>
              </div>
            </div>
          )}
        </div>
        <div className="text-muted-foreground">
          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </div>
      </div>

      {isExpanded && (
        <div className="space-y-4 border-t border-border/70 px-5 py-5 animate-in slide-in-from-top-2">
          <p className="text-sm text-muted-foreground">
            {t('settings.ai.sections.assignmentsDesc')}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <SettingsInset className="flex h-full flex-col gap-4">
              <div className="flex min-h-[88px] items-start gap-3">
                <div className="rounded-full bg-primary/10 p-2 text-primary">
                  <Bot className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-medium">{t('settings.ai.roles.system')}</h3>
                  <p className="text-xs text-muted-foreground">
                    {t('settings.ai.hints.assignSystem')}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">
                  {t('settings.ai.modelTypes.llm')}
                </label>
                <ModelSelector
                  modelType="llm"
                  value={bindings?.assistant?.llmModelId ?? null}
                  onChange={handleAssistantLlmChange}
                  disabled={updateBindingsMutation.isPending}
                />
              </div>
            </SettingsInset>

            <SettingsInset className="flex h-full flex-col gap-4">
              <div className="flex min-h-[88px] items-start gap-3">
                <div className="rounded-full bg-primary/10 p-2 text-primary">
                  <BrainCircuit className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-medium">{t('settings.ai.roles.lightrag')}</h3>
                  <p className="text-xs text-muted-foreground">
                    {t('settings.ai.hints.assignLightrag')}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">
                  {t('settings.ai.modelTypes.llm')}
                </label>
                <ModelSelector
                  modelType="llm"
                  value={bindings?.lightrag?.llmModelId ?? null}
                  onChange={handleLightragLlmChange}
                  disabled={updateBindingsMutation.isPending}
                />
              </div>
            </SettingsInset>

            <SettingsInset className="flex h-full flex-col gap-4">
              <div className="flex min-h-[88px] items-start gap-3">
                <div className="rounded-full bg-primary/10 p-2 text-primary">
                  <Sparkles className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-medium">{t('settings.ai.roles.workflowCopilot')}</h3>
                  <p className="text-xs text-muted-foreground">
                    {t('settings.ai.hints.assignWorkflowCopilot')}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">
                  {t('settings.ai.modelTypes.llm')}
                </label>
                <ModelSelector
                  modelType="llm"
                  value={bindings?.workflowCopilot?.llmModelId ?? null}
                  onChange={handleWorkflowCopilotLlmChange}
                  disabled={updateBindingsMutation.isPending}
                />
              </div>
            </SettingsInset>
          </div>
        </div>
      )}
    </div>
  )
}
