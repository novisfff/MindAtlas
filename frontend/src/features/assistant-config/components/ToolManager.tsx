import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, Power, Pencil, Trash2, Globe } from 'lucide-react'
import {
  useToolsQuery,
  useCreateToolMutation,
  useUpdateToolMutation,
  useDeleteToolMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ToolEditor } from './ToolEditor'
import type { AssistantTool, CreateToolRequest, UpdateToolRequest } from '../api/tools'

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
  const { data: tools = [], isLoading } = useToolsQuery()
  const createMutation = useCreateToolMutation()
  const updateMutation = useUpdateToolMutation()
  const deleteMutation = useDeleteToolMutation()

  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  const [error, setError] = useState<string | null>(null)

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

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const systemTools = tools.filter((t) => t.isSystem)
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
      {systemTools.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-muted-foreground">
            {t('settings.tools.systemTools')} ({systemTools.length})
          </h4>
          <div className="space-y-2">
            {systemTools.map((tool) => (
              <ToolItem
                key={tool.id}
                tool={tool}
                onEdit={() => { }}
                onDelete={() => { }}
                onToggle={() => handleToggle(tool)}
                isToggling={
                  updateMutation.isPending &&
                  updateMutation.variables?.id === tool.id
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
