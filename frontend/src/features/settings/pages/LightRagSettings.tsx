import { useEffect, useMemo, useState } from 'react'
import { Loader2, Lock, Save, TestTube2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { uiChrome } from '@/components/ui/styles'
import { useInitializationStatusQuery } from '@/features/initialization/queries'
import {
  InputField,
  Label,
  RuntimeCapabilityMeta,
  SecretHint,
  ToggleCard,
  useRuntimeConfigQuery,
  useUpdateRuntimeConfigMutation,
  useValidateRuntimeConfigMutation,
  type RuntimeKnowledgeGraphConfigRequest,
  type RuntimeKnowledgeGraphConfigResponse,
} from '@/features/system-setup'
import {
  SettingsInset,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'
import {
  getDefaultLightRagSummaryLanguage,
  isLightRagEmbeddingDimLocked,
  isKnowledgeGraphRerankEnabled,
  isLightRagEmbeddingHostLocked,
  isLightRagEmbeddingModelLocked,
  isLightRagSummaryLanguageLocked,
  validateKnowledgeGraphCapability,
} from '@/features/system-setup/runtimeRules'
import { useAppStore } from '@/stores/app-store'
import { cn } from '@/lib/utils'

interface LightRagDraft extends RuntimeKnowledgeGraphConfigResponse {
  neo4jPassword: string
  embeddingApiKey: string
  rerankApiKey: string
  rerankEnabled: boolean
}

function createDraft(value: RuntimeKnowledgeGraphConfigResponse): LightRagDraft {
  return {
    ...value,
    llmModelName: value.llmModelName ?? '',
    embeddingModelName: value.embeddingModelName ?? '',
    neo4jPassword: '',
    embeddingApiKey: '',
    rerankApiKey: '',
    rerankEnabled: isKnowledgeGraphRerankEnabled({
      ...value,
      rerankApiKey: '',
    }),
  }
}

function LockedField({
  label,
  value,
  hint,
  lockLabel,
}: {
  label: string
  value: string
  hint: string
  lockLabel: string
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className={cn(uiChrome.inset, 'border border-amber-200/80 bg-amber-50/80 px-4 py-3 dark:border-amber-500/20 dark:bg-amber-500/10')}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm font-medium text-foreground">{value || '-'}</span>
          <Badge variant="outline" className="border-amber-200/80 bg-white/80 text-amber-700 dark:border-amber-500/20 dark:bg-background/70 dark:text-amber-200">
            <Lock className="mr-1 h-3.5 w-3.5" />
            {lockLabel}
          </Badge>
        </div>
        <p className="mt-2 text-xs leading-5 text-amber-800 dark:text-amber-200">{hint}</p>
      </div>
    </div>
  )
}

function CapabilityRunBadge({
  active,
  activeLabel,
  inactiveLabel,
}: {
  active: boolean
  activeLabel: string
  inactiveLabel: string
}) {
  return (
    <Badge
      variant="outline"
      className={
        active
          ? 'border-emerald-200/80 bg-emerald-50/80 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200'
          : 'border-amber-200/80 bg-amber-50/80 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200'
      }
    >
      {active ? activeLabel : inactiveLabel}
    </Badge>
  )
}

export function LightRagSettingsPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const locale = useAppStore((state) => state.locale)
  const runtimeConfigQuery = useRuntimeConfigQuery()
  const initializationStatusQuery = useInitializationStatusQuery()
  const updateMutation = useUpdateRuntimeConfigMutation('knowledge_graph')
  const validateMutation = useValidateRuntimeConfigMutation('knowledge_graph')
  const [draft, setDraft] = useState<LightRagDraft | null>(null)
  const [isDirty, setIsDirty] = useState(false)

  const current = runtimeConfigQuery.data?.knowledgeGraph ?? null
  const initialized = Boolean(initializationStatusQuery.data?.initialized)

  useEffect(() => {
    if (!current) return
    setDraft((existing) => (existing && isDirty ? existing : createDraft(current)))
  }, [current, isDirty])

  const validationMessages = useMemo(
    () => ({
      fieldLabel: (key: string) => t(key),
      completeField: (field: string) => t('initialization.validation.completeField', { field }),
    }),
    [t]
  )

  const summaryLanguageLocked = draft
    ? isLightRagSummaryLanguageLocked(initialized, draft.summaryLanguage)
    : false
  const embeddingModelLocked = draft
    ? isLightRagEmbeddingModelLocked(
        initialized,
        draft.embeddingModelId ? String(draft.embeddingModelId) : null,
        draft.embeddingModelName
      )
    : false
  const embeddingHostLocked = draft
    ? isLightRagEmbeddingHostLocked(initialized, draft.embeddingHost)
    : false
  const embeddingDimLocked = draft
    ? isLightRagEmbeddingDimLocked(initialized, draft.embeddingDim)
    : false

  const patchDraft = (patch: Partial<LightRagDraft>) => {
    setDraft((currentDraft) => (currentDraft ? { ...currentDraft, ...patch } : currentDraft))
    setIsDirty(true)
  }

  const buildPayload = (): RuntimeKnowledgeGraphConfigRequest | null => {
    if (!draft) return null

    const payload: RuntimeKnowledgeGraphConfigRequest = {
      workspace: draft.workspace.trim(),
      llmModelName: (draft.llmModelName ?? '').trim(),
    }

    if (!summaryLanguageLocked) {
      payload.summaryLanguage = draft.summaryLanguage.trim() || getDefaultLightRagSummaryLanguage(locale)
    }
    if (!embeddingHostLocked) {
      payload.embeddingHost = draft.embeddingHost.trim()
    }
    if (draft.embeddingApiKey.trim() || !draft.embeddingApiKeyState.configured) {
      payload.embeddingApiKey = draft.embeddingApiKey.trim()
    }

    if (draft.rerankEnabled) {
      payload.rerankModel = draft.rerankModel.trim()
      payload.rerankHost = draft.rerankHost.trim()
      payload.rerankRequestFormat = draft.rerankRequestFormat.trim() || 'standard'
      if (draft.rerankApiKey.trim() || !draft.rerankApiKeyState.configured) {
        payload.rerankApiKey = draft.rerankApiKey.trim()
      }
    } else {
      payload.rerankModel = ''
      payload.rerankHost = ''
      payload.rerankApiKey = ''
      payload.rerankRequestFormat = ''
    }

    return payload
  }

  const getValidationError = () => {
    if (!draft) return null
    return validateKnowledgeGraphCapability(draft, validationMessages, draft.rerankEnabled)
  }

  const handleValidate = async () => {
    if (!current?.enabled) return

    const validationError = getValidationError()
    if (validationError) {
      toast.error(validationError)
      return
    }

    const payload = buildPayload()
    if (!payload) return

    try {
      const response = await validateMutation.mutateAsync(payload)
      if (!response.ok) {
        toast.error(response.message || Object.values(response.fieldErrors)[0] || t('systemSetup.messages.validationFailed'))
        return
      }
      toast.success(response.message || t('systemSetup.messages.validationPassed'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('systemSetup.messages.validationFailed'))
    }
  }

  const handleSave = async () => {
    if (!current?.enabled) return

    const validationError = getValidationError()
    if (validationError) {
      toast.error(validationError)
      return
    }

    const payload = buildPayload()
    if (!payload) return

    try {
      await updateMutation.mutateAsync(payload)
      setIsDirty(false)
      toast.success(t('systemSetup.messages.saved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('messages.error'))
    }
  }

  if (runtimeConfigQuery.isLoading || initializationStatusQuery.isLoading || !draft || !current) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    )
  }

  const isBusy = updateMutation.isPending || validateMutation.isPending
  const isStarted = current.enabled
  const displaySummary = isStarted
    ? current.effectiveSummary
    : t('systemSetup.detailPages.lightrag.notStartedSummary')

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.lightRag')}
        description={t('pages.settings.lightRagDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void handleValidate()
              }}
              disabled={isBusy || !isStarted}
            >
              {validateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}
              {t('systemSetup.actions.validate')}
            </Button>
            <Button
              type="button"
              onClick={() => {
                void handleSave()
              }}
              disabled={isBusy || !isStarted}
            >
              {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {t('common.save')}
            </Button>
          </>
        }
      />

      <SettingsSection>
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <CapabilityRunBadge
              active={isStarted}
              activeLabel={t('systemSetup.detailPages.status.started')}
              inactiveLabel={t('systemSetup.detailPages.status.notStarted')}
            />
            <RuntimeCapabilityMeta module={current} skipped={false} t={t} />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-semibold text-foreground">
              {t('systemSetup.detailPages.effectiveSummary')}
            </p>
            <p className="text-sm leading-6 text-muted-foreground">{displaySummary}</p>
          </div>
          <SettingsInset className="text-sm leading-6 text-muted-foreground">
            {t('systemSetup.detailPages.lightrag.deploymentManaged')}
          </SettingsInset>
        </div>
      </SettingsSection>

      {!isStarted ? (
        <SettingsSection className="border border-amber-200/80 bg-amber-50/72 dark:border-amber-500/20 dark:bg-amber-500/10">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-amber-950 dark:text-amber-100">
              {t('systemSetup.detailPages.lightrag.unavailableTitle')}
            </h2>
            <p className="text-sm leading-6 text-amber-900 dark:text-amber-200">
              {t('systemSetup.detailPages.lightrag.unavailableDescription')}
            </p>
          </div>
        </SettingsSection>
      ) : (
        <>
          <SettingsSection>
            <div className="space-y-5">
              <div className="space-y-2">
                <h2 className="text-lg font-semibold text-foreground">
                  {t('systemSetup.detailPages.lightrag.sections.embedding')}
                </h2>
                <p className="text-sm leading-6 text-muted-foreground">
                  {t('systemSetup.detailPages.lightrag.embeddingHint')}
                </p>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                {summaryLanguageLocked ? (
                  <LockedField
                    label={t('systemSetup.forms.knowledgeGraph.summaryLanguage.label')}
                    value={draft.summaryLanguage || getDefaultLightRagSummaryLanguage(locale)}
                    hint={t('systemSetup.detailPages.lightrag.summaryLanguageLocked')}
                    lockLabel={t('systemSetup.detailPages.lockedBadge')}
                  />
                ) : (
                  <InputField
                    label={t('systemSetup.forms.knowledgeGraph.summaryLanguage.label')}
                    value={draft.summaryLanguage}
                    onChange={(summaryLanguage) => patchDraft({ summaryLanguage })}
                    placeholder={t('systemSetup.forms.knowledgeGraph.summaryLanguage.placeholder')}
                  />
                )}

                <LockedField
                  label={t('systemSetup.forms.knowledgeGraph.embeddingModelName.label')}
                  value={draft.embeddingModelName || draft.embeddingModelId || '-'}
                  hint={t('systemSetup.detailPages.lightrag.embeddingModelLocked')}
                  lockLabel={t('systemSetup.detailPages.lockedBadge')}
                />
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                {embeddingHostLocked ? (
                  <LockedField
                    label={t('systemSetup.forms.knowledgeGraph.embeddingHost.label')}
                    value={draft.embeddingHost}
                    hint={t('systemSetup.detailPages.lightrag.embeddingHostLocked')}
                    lockLabel={t('systemSetup.detailPages.lockedBadge')}
                  />
                ) : (
                  <InputField
                    label={t('systemSetup.forms.knowledgeGraph.embeddingHost.label')}
                    value={draft.embeddingHost}
                    onChange={(embeddingHost) => patchDraft({ embeddingHost })}
                    placeholder={t('systemSetup.forms.knowledgeGraph.embeddingHost.placeholder')}
                  />
                )}

                {embeddingDimLocked ? (
                  <LockedField
                    label={t('systemSetup.forms.knowledgeGraph.embeddingDim.label')}
                    value={draft.embeddingDim ? String(draft.embeddingDim) : '-'}
                    hint={t('systemSetup.detailPages.lightrag.embeddingDimLocked')}
                    lockLabel={t('systemSetup.detailPages.lockedBadge')}
                  />
                ) : null}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <InputField
                    type="password"
                    label={t('systemSetup.forms.knowledgeGraph.embeddingApiKey.label')}
                    value={draft.embeddingApiKey}
                    onChange={(embeddingApiKey) => patchDraft({ embeddingApiKey })}
                    placeholder={t('systemSetup.forms.knowledgeGraph.embeddingApiKey.placeholder')}
                  />
                  <SecretHint
                    value={draft.embeddingApiKey}
                    state={draft.embeddingApiKeyState}
                    hint={t('systemSetup.forms.secret.keepExisting')}
                  />
                </div>
              </div>
            </div>
          </SettingsSection>

          <SettingsSection>
            <div className="space-y-5">
              <div className="space-y-2">
                <h2 className="text-lg font-semibold text-foreground">
                  {t('systemSetup.detailPages.lightrag.sections.rerank')}
                </h2>
                <p className="text-sm leading-6 text-muted-foreground">
                  {t('systemSetup.detailPages.lightrag.rerankHint')}
                </p>
              </div>

              <ToggleCard
                label={t('systemSetup.detailPages.lightrag.rerankToggleLabel')}
                description={t('systemSetup.detailPages.lightrag.rerankToggleDescription')}
                checked={draft.rerankEnabled}
                onCheckedChange={(rerankEnabled) =>
                  patchDraft({
                    rerankEnabled,
                    ...(rerankEnabled
                      ? {}
                      : {
                          rerankModel: '',
                          rerankHost: '',
                          rerankApiKey: '',
                          rerankRequestFormat: 'standard',
                        }),
                  })
                }
              />

              <SettingsInset className="grid gap-4 md:grid-cols-2">
                <InputField
                  label={t('systemSetup.forms.knowledgeGraph.rerankModel.label')}
                  value={draft.rerankModel}
                  onChange={(rerankModel) => patchDraft({ rerankModel })}
                  placeholder={t('systemSetup.forms.knowledgeGraph.rerankModel.placeholder')}
                  disabled={!draft.rerankEnabled}
                />
                <InputField
                  label={t('systemSetup.forms.knowledgeGraph.rerankHost.label')}
                  value={draft.rerankHost}
                  onChange={(rerankHost) => patchDraft({ rerankHost })}
                  placeholder="https://your-rerank-host/v1"
                  disabled={!draft.rerankEnabled}
                />
                <div className="space-y-2">
                  <InputField
                    type="password"
                    label={t('systemSetup.forms.knowledgeGraph.rerankApiKey.label')}
                    value={draft.rerankApiKey}
                    onChange={(rerankApiKey) => patchDraft({ rerankApiKey })}
                    placeholder={t('systemSetup.forms.knowledgeGraph.rerankApiKey.placeholder')}
                    disabled={!draft.rerankEnabled}
                  />
                  <SecretHint
                    value={draft.rerankApiKey}
                    state={draft.rerankApiKeyState}
                    hint={t('systemSetup.forms.secret.keepExisting')}
                  />
                </div>
                <InputField
                  label={t('systemSetup.forms.knowledgeGraph.rerankRequestFormat.label')}
                  value={draft.rerankRequestFormat}
                  onChange={(rerankRequestFormat) => patchDraft({ rerankRequestFormat })}
                  disabled={!draft.rerankEnabled}
                />
              </SettingsInset>
            </div>
          </SettingsSection>
        </>
      )}
    </SettingsPageShell>
  )
}
