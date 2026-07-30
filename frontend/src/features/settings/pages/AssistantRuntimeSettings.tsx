import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Bot, Loader2, Power } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import {
  AssistantRuntimeActivationCard,
  useAssistantReadinessDiagnosticsQuery,
  useAssistantRolloutActivationReadinessQuery,
  useAssistantRolloutsQuery,
  useSetAssistantNewRunsEnabledMutation,
} from '@/features/assistant-runtime'
import type { RolloutRevisionSummary } from '@/features/assistant-runtime/api/runtime'
import {
  SettingsBadge,
  SettingsInset,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { isApiError } from '@/lib/api/client'
import { createRuntimeRequestId } from '@/features/assistant-runtime/requestId'

function latestPreparedRollout(
  revisions: RolloutRevisionSummary[],
  activeRolloutRevisionId: string | null,
): RolloutRevisionSummary | null {
  return revisions.find((revision) => revision.rolloutRevisionId !== activeRolloutRevisionId) ?? null
}

export function AssistantRuntimeSettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const rolloutsQuery = useAssistantRolloutsQuery()
  const diagnosticsQuery = useAssistantReadinessDiagnosticsQuery()
  const newRunsMutation = useSetAssistantNewRunsEnabledMutation()
  const [newRunsError, setNewRunsError] = useState<string | null>(null)

  const control = rolloutsQuery.data?.control ?? null
  const revisions = rolloutsQuery.data?.revisions ?? []
  const activeRollout = useMemo(
    () =>
      revisions.find(
        (revision) => revision.rolloutRevisionId === control?.activeRolloutRevisionId,
      ) ?? null,
    [control?.activeRolloutRevisionId, revisions],
  )
  const preparedRollout = useMemo(
    () => latestPreparedRollout(revisions, control?.activeRolloutRevisionId ?? null),
    [control?.activeRolloutRevisionId, revisions],
  )
  const preparedRolloutRevisionId = preparedRollout?.rolloutRevisionId ?? null
  const candidateReadinessQuery = useAssistantRolloutActivationReadinessQuery(
    preparedRolloutRevisionId,
  )

  async function refreshRuntimeControl() {
    const refreshes: Promise<unknown>[] = [
      rolloutsQuery.refetch(),
      diagnosticsQuery.refetch(),
    ]
    if (preparedRolloutRevisionId) {
      refreshes.push(candidateReadinessQuery.refetch())
    }
    await Promise.all(refreshes)
  }

  async function handleNewRunsChange(enabled: boolean) {
    if (!control || newRunsMutation.isPending) return
    setNewRunsError(null)
    try {
      await newRunsMutation.mutateAsync({
        enabled,
        expectedControlRevision: control.controlRevision,
        requestId: createRuntimeRequestId(),
        reason: enabled ? 'enable new Main Agent runs' : 'disable new Main Agent runs',
      })
      await refreshRuntimeControl()
    } catch (error) {
      const status = isApiError(error)
        ? error.status
        : (error as { status?: number } | null)?.status
      if (status === 409) {
        setNewRunsError(t('assistantRuntime.settings.newRuns.conflict'))
        try {
          await refreshRuntimeControl()
        } catch {
          // Keep the conflict state visible; the durable server value remains authoritative.
        }
        return
      }
      setNewRunsError(t('assistantRuntime.settings.newRuns.error'))
    }
  }

  return (
    <SettingsPageShell className="space-y-6">
      <SettingsPageHeader
        title={t('assistantRuntime.settings.title')}
        description={t('assistantRuntime.settings.description')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />

      {rolloutsQuery.isLoading ? (
        <SettingsSection>
          <div className="flex items-center gap-3 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('assistantRuntime.settings.loading')}
          </div>
        </SettingsSection>
      ) : null}

      {rolloutsQuery.isError ? (
        <SettingsSection>
          <div className="flex items-start gap-2 text-sm text-destructive" role="alert">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {t('assistantRuntime.settings.loadError')}
          </div>
        </SettingsSection>
      ) : null}

      {!rolloutsQuery.isLoading && !rolloutsQuery.isError ? (
        <>
          <SettingsSection className="space-y-5">
            <SettingsSectionHeader
              title={t('assistantRuntime.settings.prepared.title')}
              description={t('assistantRuntime.settings.prepared.description')}
            />
            {preparedRollout ? (
              <div className="space-y-4">
                <SettingsInset className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      {preparedRollout.revisionLabel}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t('assistantRuntime.settings.rollout.build', {
                        build: preparedRollout.buildRevision,
                      })}
                    </p>
                  </div>
                  <SettingsBadge>{t('assistantRuntime.settings.prepared.badge')}</SettingsBadge>
                </SettingsInset>
                <AssistantRuntimeActivationCard
                  preparedRolloutRevisionId={preparedRollout.rolloutRevisionId}
                  rolloutControlRevision={control?.controlRevision ?? null}
                  diagnostics={diagnosticsQuery.data ?? null}
                  candidateReadiness={candidateReadinessQuery.data ?? null}
                  onActivated={() => {
                    void refreshRuntimeControl()
                  }}
                />
              </div>
            ) : (
              <SettingsInset>
                <p className="text-sm text-muted-foreground">
                  {t('assistantRuntime.settings.prepared.empty')}
                </p>
              </SettingsInset>
            )}
          </SettingsSection>

          <SettingsSection>
            <SettingsSectionHeader
              title={t('assistantRuntime.settings.active.title')}
              description={t('assistantRuntime.settings.active.description')}
            />
            <SettingsInset className="mt-5 flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">
                  {activeRollout?.revisionLabel ?? t('assistantRuntime.settings.active.empty')}
                </p>
                {activeRollout ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('assistantRuntime.settings.rollout.build', {
                      build: activeRollout.buildRevision,
                    })}
                  </p>
                ) : null}
              </div>
              <SettingsBadge className={activeRollout ? 'text-emerald-700' : ''}>
                <Bot className="mr-1 h-3.5 w-3.5" />
                {activeRollout
                  ? t('assistantRuntime.settings.active.badge')
                  : t('assistantRuntime.settings.active.inactiveBadge')}
              </SettingsBadge>
            </SettingsInset>
          </SettingsSection>

          <SettingsSection>
            <SettingsSectionHeader
              title={t('assistantRuntime.settings.newRuns.title')}
              description={t('assistantRuntime.settings.newRuns.description')}
            />
            <SettingsInset className="mt-5 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">
                  {control?.newRunsEnabled
                    ? t('assistantRuntime.settings.newRuns.enabled')
                    : t('assistantRuntime.settings.newRuns.disabled')}
                </p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t('assistantRuntime.settings.newRuns.hint')}
                </p>
              </div>
              <Switch
                checked={Boolean(control?.newRunsEnabled)}
                disabled={!control || newRunsMutation.isPending}
                aria-label={t('assistantRuntime.settings.newRuns.switchLabel')}
                className="data-[state=checked]:bg-emerald-500"
                onCheckedChange={(enabled) => {
                  void handleNewRunsChange(enabled)
                }}
              />
            </SettingsInset>
            {newRunsError ? (
              <div
                className="mt-4 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                role="alert"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                {newRunsError}
              </div>
            ) : null}
            <div className="mt-4 flex justify-end">
              <Button type="button" variant="outline" onClick={() => void refreshRuntimeControl()}>
                <Power className="h-4 w-4" />
                {t('common.refresh')}
              </Button>
            </div>
          </SettingsSection>
        </>
      ) : null}
    </SettingsPageShell>
  )
}
