import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, Power, Pencil, Trash2, Globe, ChevronDown, ChevronRight } from 'lucide-react'
import {
  useToolsQuery,
  useSystemToolDefinitionsQuery,
  useCreateToolMutation,
  useUpdateToolMutation,
  useUpdateSystemToolEnabledMutation,
  useDeleteToolMutation,
} from '../queries'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ToolEditor } from './ToolEditor'
import type { AssistantTool, SystemToolDefinition, CreateToolRequest, UpdateToolRequest } from '../api/tools'
import { uiChrome } from '@/components/ui/styles'
import {
  SettingsBadge,
  SettingsEmptyState,
  SettingsInset,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'

// 系统工具展示组件（支持展开/收起）
interface SystemToolItemProps {
  tool: SystemToolDefinition
  onToggle: () => void
  isToggling: boolean
}

function ToolStateBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation()

  return (
    <SettingsBadge
      className={cn(
        'gap-1.5',
        enabled
          ? 'border-emerald-200/80 bg-emerald-50/90 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200'
          : 'border-slate-200/80 bg-slate-100/90 text-slate-600 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-300',
      )}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          enabled ? 'bg-emerald-500' : 'bg-slate-400 dark:bg-slate-500',
        )}
      />
      {enabled ? t('settings.tools.enabledStateOn') : t('settings.tools.enabledStateOff')}
    </SettingsBadge>
  )
}

function SystemToolItem({ tool, onToggle, isToggling }: SystemToolItemProps) {
  const { t } = useTranslation()
  const [isExpanded, setIsExpanded] = useState(false)
  const displayName = tool.displayName || tool.name
  const displayDescription = tool.displayDescription || tool.description || t('settings.tools.noDescription')
  const hasDetailContent = Boolean(
    (tool.inputParams && tool.inputParams.length > 0)
    || (tool.outputParams && tool.outputParams.length > 0)
    || tool.returns
  )

  return (
    <div
      className={cn(
        uiChrome.card,
        'overflow-hidden p-4 transition-colors',
        'border-slate-200/80 bg-slate-50/40 hover:border-slate-300/80 dark:border-slate-800/80 dark:bg-white/[0.02]'
      )}
    >
      <div className="flex items-start gap-4">
        <button
          type="button"
          onClick={onToggle}
          disabled={isToggling}
          title={tool.enabled ? t('settings.tools.disable') : t('settings.tools.enable')}
          className={cn(
            uiChrome.control,
            'flex h-11 w-11 items-center justify-center transition-colors shadow-none',
            tool.enabled
              ? 'border-emerald-200/80 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200'
              : 'border-slate-200/80 bg-slate-100/90 text-slate-500 hover:bg-slate-200/70 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-300 dark:hover:bg-slate-700/80 dark:hover:text-slate-100'
          )}
        >
          <Power className={cn('w-5 h-5', isToggling && 'animate-pulse')} />
        </button>

        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="min-w-0 flex-1 text-left"
        >
          <div className="flex flex-wrap items-center gap-2">
            {hasDetailContent ? (
              isExpanded ? (
                <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              )
            ) : null}
            <h4 className="truncate font-medium text-foreground">{displayName}</h4>
            {displayName !== tool.name ? (
              <code className="rounded-full border border-border/70 bg-background/90 px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
                {tool.name}
              </code>
            ) : null}
            <ToolStateBadge enabled={tool.enabled} />
            <SettingsBadge>
              {t('settings.tools.system')}
            </SettingsBadge>
          </div>
          <p className={cn('mt-1 text-sm leading-6 text-muted-foreground', hasDetailContent ? 'pl-6' : '')}>
            {displayDescription}
          </p>
        </button>
      </div>

      {isExpanded && hasDetailContent ? (
        <div className="mt-4 border-t border-border/70 pt-4">
          <SettingsInset className="space-y-4">
            {tool.inputParams && tool.inputParams.length > 0 ? (
              <div className="space-y-2">
                <h5 className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  {t('settings.tools.inputParams')}
                </h5>
                <div className="space-y-1.5">
                  {tool.inputParams.map((param) => (
                    <div
                      key={param.name}
                      className="flex flex-wrap items-start gap-2 rounded-[12px] border border-border/70 bg-background/90 px-3 py-2 text-xs"
                    >
                      <code className="font-mono text-primary">
                        {param.name}
                      </code>
                      <span className="text-muted-foreground">
                        ({param.paramType})
                      </span>
                      {param.required && (
                        <span className="text-red-500">*</span>
                      )}
                      {param.description && (
                        <span className="text-foreground/70">{param.description}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {tool.outputParams && tool.outputParams.length > 0 ? (
              <div className="space-y-2">
                <h5 className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  {t('settings.tools.outputParams')}
                </h5>
                <div className="space-y-1.5">
                  {tool.outputParams.map((param) => (
                    <div
                      key={param.name}
                      className="flex flex-wrap items-start gap-2 rounded-[12px] border border-border/70 bg-background/90 px-3 py-2 text-xs"
                    >
                      <code className="font-mono text-primary">
                        {param.name}
                      </code>
                      <span className="text-muted-foreground">
                        ({param.paramType})
                      </span>
                      {param.description && (
                      <span className="text-foreground/70">{param.description}</span>
                    )}
                  </div>
                  ))}
                </div>
              </div>
            ) : null}

            {tool.returns && (!tool.outputParams || tool.outputParams.length === 0) ? (
              <div className="space-y-2">
                <h5 className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  {t('settings.tools.returns')}
                </h5>
                <div className="rounded-[12px] border border-border/70 bg-background/90 px-3 py-2 text-xs leading-6 text-foreground/70">
                  {tool.returns}
                </div>
              </div>
            ) : null}
          </SettingsInset>
        </div>
      ) : null}
    </div>
  )
}

interface ToolItemProps {
  tool: AssistantTool
  onEdit: () => void
  onDelete: () => void
  onToggle: () => void
  isToggling: boolean
}

function ToolItem({ tool, onEdit, onDelete, onToggle, isToggling }: ToolItemProps) {
  const { t } = useTranslation()

  return (
    <div
      className={cn(
        uiChrome.card,
        'flex items-start gap-4 p-4 transition-colors',
        'border-slate-200/80 bg-slate-50/40 hover:border-slate-300/80 dark:border-slate-800/80 dark:bg-white/[0.02]',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        disabled={isToggling}
        title={tool.enabled ? t('settings.tools.disable') : t('settings.tools.enable')}
        className={cn(
          uiChrome.control,
          'flex h-11 w-11 items-center justify-center transition-colors shadow-none',
          tool.enabled
            ? 'border-emerald-200/80 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200'
            : 'border-slate-200/80 bg-slate-100/90 text-slate-500 hover:bg-slate-200/70 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-300 dark:hover:bg-slate-700/80 dark:hover:text-slate-100',
        )}
      >
        <Power className={`w-5 h-5 ${isToggling ? 'animate-pulse' : ''}`} />
      </button>

      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="truncate font-medium text-foreground">{tool.name}</h4>
          <ToolStateBadge enabled={tool.enabled} />
          <SettingsBadge>{tool.isSystem ? t('settings.tools.system') : t('settings.tools.custom')}</SettingsBadge>
          {tool.kind === 'remote' && (
            <SettingsBadge className="gap-1">
              <Globe className="h-3.5 w-3.5" />
              Remote
            </SettingsBadge>
          )}
        </div>
        <p className="line-clamp-2 text-sm leading-6 text-muted-foreground">
          {tool.description || t('settings.tools.noDescription')}
        </p>
        {tool.endpointUrl && (
          <p className="truncate font-mono text-xs text-muted-foreground">
            {tool.httpMethod || 'POST'} {tool.endpointUrl}
          </p>
        )}
      </div>

      <div className="flex items-center gap-1">
        {!tool.isSystem && (
          <>
            <Button
              type="button"
              onClick={onEdit}
              title={t('common.edit')}
              variant="ghost"
              size="icon"
            >
              <Pencil className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              onClick={onDelete}
              title={t('common.delete')}
              variant="ghost"
              size="icon"
              className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

export function ToolManager() {
  const { t } = useTranslation()
  const { data: tools = [], isLoading: isLoadingTools } = useToolsQuery()
  const { data: systemToolDefs = [], isLoading: isLoadingDefs } = useSystemToolDefinitionsQuery()
  const createMutation = useCreateToolMutation()
  const updateMutation = useUpdateToolMutation()
  const updateSystemToolMutation = useUpdateSystemToolEnabledMutation()
  const deleteMutation = useDeleteToolMutation()

  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  const [error, setError] = useState<string | null>(null)

  // 系统工具启用状态切换（仅保存 enabled 覆盖；系统工具定义不落库）
  const handleSystemToolToggle = (toolDef: SystemToolDefinition) => {
    updateSystemToolMutation.mutate({ name: toolDef.name, enabled: !toolDef.enabled })
  }

  const handleToggle = (tool: AssistantTool) => {
    updateMutation.mutate({
      id: tool.id,
      data: { enabled: !tool.enabled },
    })
  }

  const handleSave = (data: CreateToolRequest | UpdateToolRequest) => {
    setError(null)
    createMutation.mutate(data as CreateToolRequest, {
      onSuccess: () => setIsAdding(false),
      onError: (err: any) => {
        const msg = err.response?.data?.message || err.message || 'Failed to create tool'
        setError(msg)
      }
    })
  }

  if (isLoadingTools || isLoadingDefs) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const customTools = tools.filter((t) => !t.isSystem)

  return (
    <div className="space-y-6">
      <SettingsSectionHeader
        title={t('settings.tools.title')}
        description={t('pages.settings.assistantToolsDesc')}
        actions={
          <Button onClick={() => setIsAdding(true)} disabled={isAdding}>
            <Plus className="h-4 w-4" />
            {t('settings.tools.addTool')}
          </Button>
        }
      />

      {isAdding && (
        <ToolEditor
          isNew
          onCancel={() => setIsAdding(false)}
          onSave={handleSave}
          isSaving={createMutation.isPending}
          errorMessage={error}
        />
      )}

      {systemToolDefs.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h4 className="text-sm font-semibold text-foreground">
              {t('settings.tools.systemTools')}
            </h4>
            <SettingsBadge>{systemToolDefs.length}</SettingsBadge>
          </div>
          <div className="space-y-3">
            {systemToolDefs.map((toolDef) => (
              <SystemToolItem
                key={toolDef.name}
                tool={toolDef}
                onToggle={() => handleSystemToolToggle(toolDef)}
                isToggling={
                  updateSystemToolMutation.isPending &&
                  updateSystemToolMutation.variables?.name === toolDef.name
                }
              />
            ))}
          </div>
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <h4 className="text-sm font-semibold text-foreground">
            {t('settings.tools.customTools')}
          </h4>
          <SettingsBadge>{customTools.length}</SettingsBadge>
        </div>
        {customTools.length === 0 && !isAdding ? (
          <SettingsEmptyState
            title={t('settings.tools.noCustomTools')}
            description={t('pages.settings.assistantToolsDesc')}
            action={
              <Button onClick={() => setIsAdding(true)}>
                <Plus className="h-4 w-4" />
                {t('settings.tools.addTool')}
              </Button>
            }
          />
        ) : (
          <div className="space-y-3">
            {customTools.map((tool) => (
              <div key={tool.id}>
                {editingId === tool.id ? (
                  <ToolEditor
                    tool={tool}
                    onCancel={() => setEditingId(null)}
                    onSave={(data) => {
                      setError(null)
                      updateMutation.mutate(
                        { id: tool.id, data },
                        {
                          onSuccess: () => setEditingId(null),
                          onError: (err: any) => {
                            const msg = err.response?.data?.message || err.message || 'Failed to update tool'
                            setError(msg)
                          }
                        }
                      )
                    }}
                    isSaving={updateMutation.isPending}
                    errorMessage={error}
                  />
                ) : (
                  <ToolItem
                    tool={tool}
                    onEdit={() => setEditingId(tool.id)}
                    onDelete={() => setDeleteId(tool.id)}
                    onToggle={() => handleToggle(tool)}
                    isToggling={
                      updateMutation.isPending &&
                      updateMutation.variables?.id === tool.id
                    }
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <SettingsInset className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm leading-6 text-muted-foreground">
          {t('pages.settings.assistantToolsDesc')}
        </p>
        <SettingsBadge>
          {t('settings.tools.customTools')}: {customTools.length}
        </SettingsBadge>
      </SettingsInset>

      <ConfirmDialog
        isOpen={!!deleteId}
        title={t('settings.tools.deleteTitle')}
        description={t('settings.tools.deleteDescription')}
        confirmText={t('common.delete')}
        variant="destructive"
        onConfirm={() =>
          deleteId &&
          deleteMutation.mutate(deleteId, { onSuccess: () => setDeleteId(null) })
        }
        onCancel={() => setDeleteId(null)}
      />
    </div>
  )
}
