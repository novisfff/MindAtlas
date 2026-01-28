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
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ToolEditor } from './ToolEditor'
import type { AssistantTool, SystemToolDefinition, CreateToolRequest, UpdateToolRequest } from '../api/tools'
import { cn } from '@/lib/utils'

// 系统工具展示组件（支持展开/收起）
interface SystemToolItemProps {
  tool: SystemToolDefinition
  onToggle: () => void
  isToggling: boolean
}

function SystemToolItem({ tool, onToggle, isToggling }: SystemToolItemProps) {
  const { t } = useTranslation()
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <div
      className={cn(
        'rounded-lg border transition-colors',
        tool.enabled ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/20' : 'hover:bg-muted/50'
      )}
    >
      {/* 主行 */}
      <div className="flex items-center gap-4 p-4">
        <button
          onClick={onToggle}
          disabled={isToggling}
          title={tool.enabled ? t('settings.tools.disable') : t('settings.tools.enable')}
          className={cn(
            'p-2 rounded-lg transition-colors',
            tool.enabled
              ? 'bg-blue-100 text-blue-600 dark:bg-blue-900 dark:text-blue-400'
              : 'bg-muted text-muted-foreground hover:bg-primary/10 hover:text-primary'
          )}
        >
          <Power className={cn('w-5 h-5', isToggling && 'animate-pulse')} />
        </button>

        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex-1 min-w-0 text-left"
        >
          <div className="flex items-center gap-2">
            {isExpanded ? (
              <ChevronDown className="w-4 h-4 text-muted-foreground flex-shrink-0" />
            ) : (
              <ChevronRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
            )}
            <h4 className="font-medium truncate">{tool.name}</h4>
            <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">
              {t('settings.tools.system')}
            </span>
          </div>
          <p className="text-sm text-muted-foreground line-clamp-2 ml-6">
            {tool.description || t('settings.tools.noDescription')}
          </p>
        </button>
      </div>

      {/* 展开详情 */}
      {isExpanded && ((tool.inputParams && tool.inputParams.length > 0) || tool.returns) && (
        <div className="px-4 pb-4 pt-0 ml-6 border-t border-blue-500/10">
          {tool.inputParams && tool.inputParams.length > 0 && (
            <div className="mt-3">
              <h5 className="text-xs font-medium text-muted-foreground mb-2">
                {t('settings.tools.inputParams')}
              </h5>
              <div className="space-y-1.5">
                {tool.inputParams.map((param) => (
                  <div
                    key={param.name}
                    className="flex items-start gap-2 text-xs bg-muted/50 rounded px-2 py-1.5"
                  >
                    <code className="font-mono text-blue-600 dark:text-blue-400 flex-shrink-0">
                      {param.name}
                    </code>
                    <span className="text-muted-foreground flex-shrink-0">
                      ({param.paramType})
                    </span>
                    {param.required && (
                      <span className="text-red-500 flex-shrink-0">*</span>
                    )}
                    {param.description && (
                      <span className="text-foreground/70">{param.description}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {tool.returns && (
            <div className="mt-3">
              <h5 className="text-xs font-medium text-muted-foreground mb-2">
                {t('settings.tools.returns')}
              </h5>
              <div className="text-xs bg-muted/50 rounded px-2 py-1.5 text-foreground/70">
                {tool.returns}
              </div>
            </div>
          )}
        </div>
      )}
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
      className={`flex items-center gap-4 p-4 rounded-lg border transition-colors ${tool.enabled
        ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/20'
        : 'hover:bg-muted/50'
        }`}
    >
      <button
        onClick={onToggle}
        disabled={isToggling}
        title={tool.enabled ? t('settings.tools.disable') : t('settings.tools.enable')}
        className={`p-2 rounded-lg transition-colors ${tool.enabled
          ? 'bg-blue-100 text-blue-600 dark:bg-blue-900 dark:text-blue-400'
          : 'bg-muted text-muted-foreground hover:bg-primary/10 hover:text-primary'
          }`}
      >
        <Power className={`w-5 h-5 ${isToggling ? 'animate-pulse' : ''}`} />
      </button>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h4 className="font-medium truncate">{tool.name}</h4>
          {tool.isSystem ? (
            <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">
              {t('settings.tools.system')}
            </span>
          ) : (
            <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">
              {t('settings.tools.custom')}
            </span>
          )}
          {tool.kind === 'remote' && (
            <Globe className="w-4 h-4 text-muted-foreground" />
          )}
        </div>
        <p className="text-sm text-muted-foreground truncate">
          {tool.description || t('settings.tools.noDescription')}
        </p>
        {tool.endpointUrl && (
          <p className="text-xs text-muted-foreground font-mono truncate">
            {tool.httpMethod || 'POST'} {tool.endpointUrl}
          </p>
        )}
      </div>

      <div className="flex items-center gap-1">
        {!tool.isSystem && (
          <>
            <button
              onClick={onEdit}
              title={t('common.edit')}
              className="p-2 rounded hover:bg-muted"
            >
              <Pencil className="w-4 h-4 text-muted-foreground" />
            </button>
            <button
              onClick={onDelete}
              title={t('common.delete')}
              className="p-2 rounded hover:bg-red-100 text-red-500"
            >
              <Trash2 className="w-4 h-4" />
            </button>
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
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{t('settings.tools.title')}</h3>
        <button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> {t('settings.tools.addTool')}
        </button>
      </div>

      {isAdding && (
        <ToolEditor
          isNew
          onCancel={() => setIsAdding(false)}
          onSave={handleSave}
          isSaving={createMutation.isPending}
          errorMessage={error}
        />
      )}

      {/* System Tools */}
      {systemToolDefs.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-muted-foreground">
            {t('settings.tools.systemTools')} ({systemToolDefs.length})
          </h4>
          <div className="space-y-2">
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

      {/* Custom Tools */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium text-muted-foreground">
          {t('settings.tools.customTools')} ({customTools.length})
        </h4>
        {customTools.length === 0 && !isAdding ? (
          <p className="text-sm text-muted-foreground py-4 text-center">
            {t('settings.tools.noCustomTools')}
          </p>
        ) : (
          <div className="space-y-2">
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
