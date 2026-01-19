import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, Power, Pencil, Trash2, ChevronDown, ChevronRight, RotateCcw } from 'lucide-react'
import {
  useSkillsQuery,
  useToolsQuery,
  useCreateSkillMutation,
  useUpdateSkillMutation,
  useDeleteSkillMutation,
  useResetSkillMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { SkillRow } from './SkillRow'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest } from '../api/skills'

interface SkillItemProps {
  skill: AssistantSkill
  onEdit: () => void
  onDelete: () => void
  onToggle: () => void
  isToggling: boolean
}

function SkillItem({ skill, onEdit, onDelete, onToggle, isToggling }: SkillItemProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      className={`rounded-lg border transition-colors ${skill.enabled
        ? 'border-purple-500 bg-purple-50 dark:bg-purple-950/20'
        : 'hover:bg-muted/50'
        }`}
    >
      <div className="flex items-center gap-4 p-4">
        <button
          onClick={() => setExpanded(!expanded)}
          className="p-1 rounded hover:bg-muted"
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronRight className="w-4 h-4" />
          )}
        </button>

        <button
          onClick={onToggle}
          disabled={isToggling}
          title={skill.enabled ? t('settings.skills.disable') : t('settings.skills.enable')}
          className={`p-2 rounded-lg transition-colors ${skill.enabled
            ? 'bg-purple-100 text-purple-600 dark:bg-purple-900 dark:text-purple-400'
            : 'bg-muted text-muted-foreground hover:bg-primary/10 hover:text-primary'
            }`}
        >
          <Power className={`w-5 h-5 ${isToggling ? 'animate-pulse' : ''}`} />
        </button>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h4 className="font-medium truncate">{skill.name}</h4>
            {skill.isSystem ? (
              <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">
                {t('settings.skills.system')}
              </span>
            ) : (
              <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">
                {t('settings.skills.custom')}
              </span>
            )}
            {skill.mode === 'agent' ? (
              <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">
                Agent
              </span>
            ) : (
              <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                {skill.steps?.length || 0} {t('settings.skills.steps')}
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground truncate">{skill.description}</p>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={onEdit}
            title={t('common.edit')}
            className="p-2 rounded hover:bg-muted"
          >
            <Pencil className="w-4 h-4 text-muted-foreground" />
          </button>
          {!skill.isSystem && (
            <button
              onClick={onDelete}
              title={t('common.delete')}
              className="p-2 rounded hover:bg-red-100 text-red-500"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 pt-0 border-t mx-4 mt-2">
          {skill.intentExamples && skill.intentExamples.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-medium text-muted-foreground mb-1">
                {t('settings.skills.intentExamples')}
              </p>
              <div className="flex flex-wrap gap-1">
                {skill.intentExamples.map((ex, i) => (
                  <span
                    key={i}
                    className="text-xs px-2 py-0.5 rounded bg-muted"
                  >
                    {ex}
                  </span>
                ))}
              </div>
            </div>
          )}

          {skill.tools && skill.tools.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-medium text-muted-foreground mb-1">
                {t('settings.skills.tools')}
              </p>
              <div className="flex flex-wrap gap-1">
                {skill.tools.map((tool, i) => (
                  <span
                    key={i}
                    className="text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
                  >
                    {tool}
                  </span>
                ))}
              </div>
            </div>
          )}

          {skill.steps && skill.steps.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-medium text-muted-foreground mb-1">
                {t('settings.skills.stepsDetail')}
              </p>
              <div className="space-y-1">
                {skill.steps
                  .sort((a, b) => a.stepOrder - b.stepOrder)
                  .map((step, i) => (
                    <div
                      key={step.id}
                      className="text-xs p-2 rounded bg-muted flex items-start gap-2"
                    >
                      <span className="font-mono text-muted-foreground">
                        {i + 1}.
                      </span>
                      <div>
                        <span className="font-medium">[{step.type}]</span>
                        {step.toolName && (
                          <span className="ml-1 text-blue-600">{step.toolName}</span>
                        )}
                        {step.instruction && (
                          <p className="text-muted-foreground mt-0.5">
                            {step.instruction}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function SkillManager() {
  const { t } = useTranslation()
  const { data: skills = [], isLoading } = useSkillsQuery()
  const { data: tools = [] } = useToolsQuery()
  const createMutation = useCreateSkillMutation()
  const updateMutation = useUpdateSkillMutation()
  const deleteMutation = useDeleteSkillMutation()
  const resetMutation = useResetSkillMutation()

  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [resetId, setResetId] = useState<string | null>(null)

  const handleToggle = (skill: AssistantSkill) => {
    updateMutation.mutate({
      id: skill.id,
      data: { enabled: !skill.enabled },
    })
  }

  const handleSave = (data: CreateSkillRequest | UpdateSkillRequest) => {
    createMutation.mutate(data as CreateSkillRequest, { onSuccess: () => setIsAdding(false) })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const systemSkills = skills.filter((s) => s.isSystem)
  const customSkills = skills.filter((s) => !s.isSystem)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{t('settings.skills.title')}</h3>
        <button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> {t('settings.skills.addSkill')}
        </button>
      </div>

      {isAdding && (
        <SkillRow
          isNew
          availableTools={tools}
          onCancel={() => setIsAdding(false)}
          onSave={handleSave}
          isSaving={createMutation.isPending}
        />
      )}

      {/* System Skills */}
      {systemSkills.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-muted-foreground">
            {t('settings.skills.systemSkills')} ({systemSkills.length})
          </h4>
          <div className="space-y-2">
            {systemSkills.map((skill) => (
              <div key={skill.id}>
                {editingId === skill.id ? (
                  <SkillRow
                    skill={skill}
                    isEditing
                    availableTools={tools}
                    onCancel={() => setEditingId(null)}
                    onSave={(data) => {
                      updateMutation.mutate(
                        { id: skill.id, data },
                        { onSuccess: () => setEditingId(null) }
                      )
                    }}
                    onReset={() => setResetId(skill.id)}
                    isSaving={updateMutation.isPending}
                  />
                ) : (
                  <SkillItem
                    skill={skill}
                    onEdit={() => setEditingId(skill.id)}
                    onDelete={() => { }}
                    onToggle={() => handleToggle(skill)}
                    isToggling={
                      updateMutation.isPending &&
                      updateMutation.variables?.id === skill.id
                    }
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Custom Skills */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium text-muted-foreground">
          {t('settings.skills.customSkills')} ({customSkills.length})
        </h4>
        {customSkills.length === 0 && !isAdding ? (
          <p className="text-sm text-muted-foreground py-4 text-center">
            {t('settings.skills.noCustomSkills')}
          </p>
        ) : (
          <div className="space-y-2">
            {customSkills.map((skill) => (
              <div key={skill.id}>
                {editingId === skill.id ? (
                  <SkillRow
                    skill={skill}
                    isEditing
                    availableTools={tools}
                    onCancel={() => setEditingId(null)}
                    onSave={(data) => {
                      updateMutation.mutate(
                        { id: skill.id, data },
                        { onSuccess: () => setEditingId(null) }
                      )
                    }}
                    isSaving={updateMutation.isPending}
                  />
                ) : (
                  <SkillItem
                    skill={skill}
                    onEdit={() => setEditingId(skill.id)}
                    onDelete={() => setDeleteId(skill.id)}
                    onToggle={() => handleToggle(skill)}
                    isToggling={
                      updateMutation.isPending &&
                      updateMutation.variables?.id === skill.id
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
        title={t('settings.skills.deleteTitle')}
        description={t('settings.skills.deleteDescription')}
        confirmText={t('common.delete')}
        variant="destructive"
        onConfirm={() =>
          deleteId &&
          deleteMutation.mutate(deleteId, { onSuccess: () => setDeleteId(null) })
        }
        onCancel={() => setDeleteId(null)}
      />

      <ConfirmDialog
        isOpen={!!resetId}
        title={t('settings.skills.resetTitle')}
        description={t('settings.skills.resetDescription')}
        confirmText={t('settings.skills.reset')}
        variant="default"
        onConfirm={() =>
          resetId &&
          resetMutation.mutate(resetId, {
            onSuccess: () => {
              setResetId(null)
              if (editingId === resetId) setEditingId(null)
            },
          })
        }
        onCancel={() => setResetId(null)}
      />
    </div>
  )
}
