import { useState } from 'react'
import { Bot, BrainCircuit, ChevronDown, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ModelSelector } from './ModelSelector'
import { useModelBindingsQuery, useUpdateBindingsMutation, useModelsQuery } from '../queries'
import { toast } from 'sonner'
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
    if (!id) return t('settings.tools.notSet')
    return allModels.find(m => m.id === id)?.name || t('settings.tools.unknown')
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

  if (isLoading) {
    return (
      <div className={cn("rounded-xl border bg-card p-4", className)}>
        <div className="h-6 w-48 bg-muted animate-pulse rounded" />
      </div>
    )
  }

  return (
    <div className={cn("rounded-xl border bg-card overflow-hidden transition-all", className)}>
      <div
        className="p-4 flex items-center justify-between cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-4 min-w-0 flex-1">
          <h3 className="font-medium text-sm shrink-0">{t('settings.ai.sections.defaultBindings')}</h3>

          {!isExpanded && bindings && (
            <div className="flex items-center gap-4 text-xs text-muted-foreground truncate opacity-80">
              <div className="flex items-center gap-1">
                <Bot className="w-3 h-3" />
                <span>{getModelName(bindings.assistant?.llmModelId)}</span>
              </div>
              <div className="w-px h-3 bg-border" />
              <div className="flex items-center gap-1">
                <BrainCircuit className="w-3 h-3" />
                <span>{getModelName(bindings.lightrag?.llmModelId)}</span>
              </div>
            </div>
          )}
        </div>
        <div className="text-muted-foreground">
          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </div>
      </div>

      {isExpanded && (
        <div className="p-4 pt-0 space-y-4 animate-in slide-in-from-top-2">
          <p className="text-sm text-muted-foreground">
            {t('settings.ai.sections.assignmentsDesc')}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* System Assistant Card */}
            <div className="rounded-xl border bg-background/50 p-4 space-y-4">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-violet-500/10 text-violet-600">
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
            </div>

            {/* LightRAG Card */}
            <div className="rounded-xl border bg-background/50 p-4 space-y-4">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-purple-500/10 text-purple-600">
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
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
