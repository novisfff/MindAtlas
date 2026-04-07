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
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ResetDangerConfirmDialog } from './ResetDangerConfirmDialog'
import { SkillRow } from './SkillRow'
import { SkillCard } from './SkillCard'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest } from '../api/skills'
import { buildSkillBindingTargets } from './skillTargetOptions'
import {
  SettingsBadge,
  SettingsEmptyState,
  SettingsInset,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'

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
    <div className="space-y-6">
      <SettingsSectionHeader
        title={t('settings.skills.title')}
        description={t('settings.skills.description')}
        actions={
          <Button onClick={() => handleOpenCreate('header')} disabled={isAdding}>
            <Plus className="h-4 w-4" />
            {t('settings.skills.addSkill')}
          </Button>
        }
      />

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

      {systemSkills.length > 0 && (
        <div className="space-y-4">
          <SettingsSectionHeader
            title={
              <span className="flex items-center gap-3">
                <span>{t('settings.skills.systemSkills')}</span>
                <SettingsBadge>{systemSkills.length}</SettingsBadge>
              </span>
            }
            description={t('settings.skills.systemSkillsDesc', { defaultValue: t('settings.skills.description') })}
            actions={
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowResetAllConfirm(true)}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                {t('settings.skills.resetAll')}
              </Button>
            }
          />

          <div className="grid gap-3">
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

      <div className="space-y-4">
        <SettingsSectionHeader
          title={
            <span className="flex items-center gap-3">
              <span>{t('settings.skills.customSkills')}</span>
              <SettingsBadge>{customSkills.length}</SettingsBadge>
            </span>
          }
          description={t('settings.skills.noCustomSkills')}
        />

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
          <SettingsEmptyState
            title={t('settings.skills.noCustomSkills')}
            description={t('pages.settings.assistantSkillsDesc')}
            action={
              <Button type="button" onClick={() => handleOpenCreate('custom')}>
                <Plus className="h-4 w-4" />
                {t('settings.skills.addSkill')}
              </Button>
            }
          />
        ) : (
          <div className="grid gap-3">
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

      <SettingsInset className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm leading-6 text-muted-foreground">
          {t('settings.skills.description')}
        </p>
        <SettingsBadge>
          {t('settings.skills.customSkills')}: {customSkills.length}
        </SettingsBadge>
      </SettingsInset>

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
