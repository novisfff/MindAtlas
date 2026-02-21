import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import {
  useAgentProfilesQuery,
  useCreateSkillMutation,
  useDeleteSkillMutation,
  useResetAllSkillsMutation,
  useResetSkillMutation,
  useSkillsQuery,
  useUpdateSkillMutation,
  useWorkflowsQuery,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ResetDangerConfirmDialog } from './ResetDangerConfirmDialog'
import { SkillRow } from './SkillRow'
import { SkillCard } from './SkillCard'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest } from '../api/skills'
import { buildSkillBindingTargets } from './skillTargetOptions'

export function SkillManager() {
  const { t } = useTranslation()
  const { data: skills = [], isLoading } = useSkillsQuery()
  const { data: workflows = [] } = useWorkflowsQuery()
  const { data: agents = [] } = useAgentProfilesQuery()
  const createMutation = useCreateSkillMutation()
  const updateMutation = useUpdateSkillMutation()
  const deleteMutation = useDeleteSkillMutation()
  const resetMutation = useResetSkillMutation()
  const resetAllMutation = useResetAllSkillsMutation()

  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [createPlacement, setCreatePlacement] = useState<'header' | 'custom'>('header')
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [resetId, setResetId] = useState<string | null>(null)
  const [showResetAllConfirm, setShowResetAllConfirm] = useState(false)
  const [expandedSkillId, setExpandedSkillId] = useState<string | null>(null)

  const systemDefaultSkill = skills.find((item) => item.name === 'general_chat')
  const defaultTargetType = systemDefaultSkill?.targetType ?? null
  const defaultTargetId = defaultTargetType === 'workflow'
    ? (systemDefaultSkill?.workflowId ?? null)
    : (systemDefaultSkill?.agentProfileId ?? null)
  const availableTargets = buildSkillBindingTargets(
    workflows,
    agents,
    {
      defaultTargetType,
      defaultTargetId,
    },
  )

  const handleResetAll = async () => {
    try {
      await resetAllMutation.mutateAsync()
      toast.success(t('settings.skills.resetAllSuccess'))
      setShowResetAllConfirm(false)
    } catch {
      toast.error(t('settings.skills.resetAllError'))
    }
  }

  const handleToggle = (skill: AssistantSkill) => {
    updateMutation.mutate({
      id: skill.id,
      data: { enabled: !skill.enabled },
    })
  }

  const handleSave = (data: CreateSkillRequest | UpdateSkillRequest) => {
    createMutation.mutate(data as CreateSkillRequest, { onSuccess: () => setIsAdding(false) })
  }

  const handleOpenCreate = (placement: 'header' | 'custom') => {
    setCreatePlacement(placement)
    setIsAdding(true)
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
  const resettingSkill = resetId ? skills.find((item) => item.id === resetId) : null

  return (
    <div className="space-y-8">
      {/* Header Actions */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h3 className="font-semibold text-lg">{t('settings.skills.title')}</h3>
          <p className="text-sm text-muted-foreground">{t('settings.skills.description')}</p>
        </div>
        <button
          onClick={() => handleOpenCreate('header')}
          disabled={isAdding}
          className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-xl bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 transition-all hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> {t('settings.skills.addSkill')}
        </button>
      </div>

      {isAdding && createPlacement === 'header' && (
        <div className="animate-in fade-in slide-in-from-top-4 duration-300">
          <SkillRow
            isNew
            availableTargets={availableTargets}
            onCancel={() => setIsAdding(false)}
            onSave={handleSave}
            isSaving={createMutation.isPending}
          />
        </div>
      )}

      {/* System Skills Section */}
      {systemSkills.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between pb-2 border-b">
            <h4 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
              {t('settings.skills.systemSkills')}
              <span className="px-1.5 py-0.5 rounded-full bg-muted text-xs">{systemSkills.length}</span>
            </h4>
            <button
              onClick={() => setShowResetAllConfirm(true)}
              className="group flex items-center gap-1.5 px-2 py-1 text-xs font-medium text-muted-foreground hover:text-orange-600 transition-colors"
              title={t('settings.skills.reset')}
            >
              <RotateCcw className="w-3.5 h-3.5 group-hover:rotate-180 transition-transform duration-500" />
              {t('settings.skills.resetAll')}
            </button>
          </div>

          <div className="grid gap-4">
            {systemSkills.map((skill) => (
              <div key={skill.id} className="transition-all duration-200">
                {editingId === skill.id ? (
                  <SkillRow
                    skill={skill}
                    isEditing
                    availableTargets={availableTargets}
                    onCancel={() => setEditingId(null)}
                    onSave={(data) => {
                      updateMutation.mutate(
                        { id: skill.id, data },
                        { onSuccess: () => setEditingId(null) },
                      )
                    }}
                    onReset={() => setResetId(skill.id)}
                    isSaving={updateMutation.isPending}
                  />
                ) : (
                  <SkillCard
                    skill={skill}
                    isExpanded={expandedSkillId === skill.id}
                    onToggleExpand={() => setExpandedSkillId(expandedSkillId === skill.id ? null : skill.id)}
                    onEdit={() => setEditingId(skill.id)}
                    onDelete={() => { }}
                    onToggleEnabled={() => handleToggle(skill)}
                    isToggling={updateMutation.isPending && updateMutation.variables?.id === skill.id}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Custom Skills Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between pb-2 border-b">
          <h4 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            {t('settings.skills.customSkills')}
            <span className="px-1.5 py-0.5 rounded-full bg-muted text-xs">{customSkills.length}</span>
          </h4>
        </div>

        {isAdding && createPlacement === 'custom' && (
          <div className="animate-in fade-in slide-in-from-top-4 duration-300">
            <SkillRow
              isNew
              availableTargets={availableTargets}
              onCancel={() => setIsAdding(false)}
              onSave={handleSave}
              isSaving={createMutation.isPending}
            />
          </div>
        )}

        {customSkills.length === 0 && !isAdding ? (
          <button
            type="button"
            onClick={() => handleOpenCreate('custom')}
            className="w-full py-12 border rounded-xl border-dashed bg-muted/10 flex flex-col items-center justify-center text-center gap-2 transition-colors hover:bg-muted/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <div className="p-3 rounded-full bg-muted/20">
              <Plus className="w-6 h-6 text-muted-foreground/70" />
            </div>
            <p className="text-sm text-muted-foreground max-w-xs">{t('settings.skills.noCustomSkills')}</p>
          </button>
        ) : (
          <div className="grid gap-4">
            {customSkills.map((skill) => (
              <div key={skill.id} className="transition-all duration-200">
                {editingId === skill.id ? (
                  <SkillRow
                    skill={skill}
                    isEditing
                    availableTargets={availableTargets}
                    onCancel={() => setEditingId(null)}
                    onSave={(data) => {
                      updateMutation.mutate(
                        { id: skill.id, data },
                        { onSuccess: () => setEditingId(null) },
                      )
                    }}
                    isSaving={updateMutation.isPending}
                  />
                ) : (
                  <SkillCard
                    skill={skill}
                    isExpanded={expandedSkillId === skill.id}
                    onToggleExpand={() => setExpandedSkillId(expandedSkillId === skill.id ? null : skill.id)}
                    onEdit={() => setEditingId(skill.id)}
                    onDelete={() => setDeleteId(skill.id)}
                    onToggleEnabled={() => handleToggle(skill)}
                    isToggling={updateMutation.isPending && updateMutation.variables?.id === skill.id}
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
        onCancel={() => setDeleteId(null)}
        onConfirm={() => {
          if (!deleteId) return
          deleteMutation.mutate(deleteId, {
            onSuccess: () => setDeleteId(null),
          })
        }}
        isLoading={deleteMutation.isPending}
      />

      <ResetDangerConfirmDialog
        open={!!resetId}
        mode="single"
        targetName={resettingSkill?.name}
        loading={resetMutation.isPending}
        onOpenChange={(open) => {
          if (!open) {
            setResetId(null)
          }
        }}
        onConfirm={() => {
          if (!resetId) return
          resetMutation.mutate(resetId, {
            onSuccess: () => {
              setResetId(null)
              toast.success(t('settings.skills.resetSuccess'))
            },
            onError: () => toast.error(t('settings.skills.resetError')),
          })
        }}
      />

      <ResetDangerConfirmDialog
        open={showResetAllConfirm}
        mode="all"
        affectedCount={systemSkills.length}
        loading={resetAllMutation.isPending}
        onOpenChange={setShowResetAllConfirm}
        onConfirm={handleResetAll}
      />
    </div>
  )
}
