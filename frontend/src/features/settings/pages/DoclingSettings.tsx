import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Loader2, Save } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  InputField,
  RuntimeCapabilityMeta,
  SecretHint,
  TextareaField,
  ToggleCard,
  useRuntimeConfigQuery,
  useUpdateRuntimeConfigMutation,
  type RuntimeDocumentParsingConfigRequest,
  type RuntimeDocumentParsingConfigResponse,
} from '@/features/system-setup'
import { validateDocumentParsingCapability } from '@/features/system-setup/runtimeRules'

interface DoclingDraft extends RuntimeDocumentParsingConfigResponse {
  pictureDescriptionApiKey: string
}

function createDraft(value: RuntimeDocumentParsingConfigResponse): DoclingDraft {
  return {
    ...value,
    pictureDescriptionApiKey: '',
  }
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
          ? 'rounded-full border-emerald-200 bg-emerald-50 text-emerald-700'
          : 'rounded-full border-amber-200 bg-amber-50 text-amber-700'
      }
    >
      {active ? activeLabel : inactiveLabel}
    </Badge>
  )
}

export function DoclingSettingsPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const runtimeConfigQuery = useRuntimeConfigQuery()
  const updateMutation = useUpdateRuntimeConfigMutation('document_parsing')
  const [draft, setDraft] = useState<DoclingDraft | null>(null)
  const [isDirty, setIsDirty] = useState(false)

  const current = runtimeConfigQuery.data?.documentParsing ?? null

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

  const patchDraft = (patch: Partial<DoclingDraft>) => {
    setDraft((currentDraft) => (currentDraft ? { ...currentDraft, ...patch } : currentDraft))
    setIsDirty(true)
  }

  const getValidationError = () => {
    if (!draft) return null
    return validateDocumentParsingCapability(draft, validationMessages)
  }

  const buildPayload = (): RuntimeDocumentParsingConfigRequest | null => {
    if (!draft) return null

    const payload: RuntimeDocumentParsingConfigRequest = {
      ocrEnabled: draft.ocrEnabled,
      ocrLangs: draft.ocrLangs.trim(),
      pictureDescriptionEnabled: draft.pictureDescriptionEnabled,
      pictureDescriptionUrl: draft.pictureDescriptionUrl.trim(),
      pictureDescriptionModel: draft.pictureDescriptionModel.trim(),
      pictureDescriptionPrompt: draft.pictureDescriptionPrompt.trim(),
      pictureDescriptionTimeoutSec: draft.pictureDescriptionTimeoutSec,
      pictureDescriptionParamsJson: draft.pictureDescriptionParamsJson.trim(),
    }

    if (draft.pictureDescriptionApiKey.trim() || !draft.pictureDescriptionApiKeyState.configured) {
      payload.pictureDescriptionApiKey = draft.pictureDescriptionApiKey.trim()
    }

    return payload
  }

  const handleSave = async () => {
    if (!current?.workerEnabled) return

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

  if (runtimeConfigQuery.isLoading || !draft || !current) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    )
  }

  const isStarted = current.workerEnabled
  const displaySummary = isStarted
    ? current.effectiveSummary
    : t('systemSetup.detailPages.docling.notStartedSummary')

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => navigate('/settings')}
            className="inline-flex items-center gap-2 text-sm text-slate-500 transition hover:text-slate-900"
          >
            <ArrowLeft className="h-4 w-4" />
            {t('common.back')}
          </button>
          <div className="space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
              {t('pages.settings.docling')}
            </h1>
            <p className="max-w-3xl text-sm leading-7 text-slate-600">
              {t('pages.settings.doclingDesc')}
            </p>
          </div>
        </div>

        <Button
          type="button"
          onClick={() => {
            void handleSave()
          }}
          disabled={updateMutation.isPending || !isStarted}
          className="rounded-2xl"
        >
          {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {t('common.save')}
        </Button>
      </div>

      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
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
            <p className="text-sm font-semibold text-slate-900">
              {t('systemSetup.detailPages.effectiveSummary')}
            </p>
            <p className="text-sm leading-6 text-slate-600">{displaySummary}</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm leading-6 text-slate-600">
            {t('systemSetup.detailPages.docling.deploymentManaged')}
          </div>
        </div>
      </section>

      {!isStarted ? (
        <section className="rounded-[28px] border border-amber-200 bg-amber-50/70 p-6 shadow-sm">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-amber-950">
              {t('systemSetup.detailPages.docling.unavailableTitle')}
            </h2>
            <p className="text-sm leading-6 text-amber-900">
              {t('systemSetup.detailPages.docling.unavailableDescription')}
            </p>
          </div>
        </section>
      ) : (
        <>
          <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div className="space-y-5">
              <div className="space-y-2">
                <h2 className="text-lg font-semibold text-slate-900">
                  {t('systemSetup.detailPages.docling.sections.ocr')}
                </h2>
                <p className="text-sm leading-6 text-slate-600">
                  {t('systemSetup.detailPages.docling.ocrHint')}
                </p>
              </div>

              <ToggleCard
                label={t('systemSetup.forms.documentParsing.ocrEnabled.label')}
                description={t('systemSetup.forms.documentParsing.ocrEnabled.description')}
                checked={draft.ocrEnabled}
                onCheckedChange={(ocrEnabled) => patchDraft({ ocrEnabled })}
              />

              <InputField
                label={t('systemSetup.forms.documentParsing.ocrLangs.label')}
                value={draft.ocrLangs}
                onChange={(ocrLangs) => patchDraft({ ocrLangs })}
                placeholder={t('systemSetup.forms.documentParsing.ocrLangs.placeholder')}
                disabled={!draft.ocrEnabled}
              />
            </div>
          </section>

          <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div className="space-y-5">
              <div className="space-y-2">
                <h2 className="text-lg font-semibold text-slate-900">
                  {t('systemSetup.detailPages.docling.sections.pictureDescription')}
                </h2>
                <p className="text-sm leading-6 text-slate-600">
                  {t('systemSetup.detailPages.docling.pictureDescriptionHint')}
                </p>
              </div>

              <ToggleCard
                label={t('systemSetup.forms.documentParsing.pictureDescriptionEnabled.label')}
                description={t('systemSetup.forms.documentParsing.pictureDescriptionEnabled.description')}
                checked={draft.pictureDescriptionEnabled}
                onCheckedChange={(pictureDescriptionEnabled) => patchDraft({ pictureDescriptionEnabled })}
              />

              <div className="grid gap-4 rounded-[24px] border border-slate-200 bg-slate-50/70 p-4 md:grid-cols-2">
                <InputField
                  label={t('systemSetup.forms.documentParsing.pictureDescriptionUrl.label')}
                  value={draft.pictureDescriptionUrl}
                  onChange={(pictureDescriptionUrl) => patchDraft({ pictureDescriptionUrl })}
                  placeholder="https://api.openai.com/v1"
                  disabled={!draft.pictureDescriptionEnabled}
                />
                <InputField
                  label={t('systemSetup.forms.documentParsing.pictureDescriptionModel.label')}
                  value={draft.pictureDescriptionModel}
                  onChange={(pictureDescriptionModel) => patchDraft({ pictureDescriptionModel })}
                  placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionModel.placeholder')}
                  disabled={!draft.pictureDescriptionEnabled}
                />
                <div className="space-y-2">
                  <InputField
                    type="password"
                    label={t('systemSetup.forms.documentParsing.pictureDescriptionApiKey.label')}
                    value={draft.pictureDescriptionApiKey}
                    onChange={(pictureDescriptionApiKey) => patchDraft({ pictureDescriptionApiKey })}
                    placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionApiKey.placeholder')}
                    disabled={!draft.pictureDescriptionEnabled}
                  />
                  <SecretHint
                    value={draft.pictureDescriptionApiKey}
                    state={draft.pictureDescriptionApiKeyState}
                    hint={t('systemSetup.forms.secret.keepExisting')}
                  />
                </div>
                <InputField
                  type="number"
                  label={t('systemSetup.forms.documentParsing.pictureDescriptionTimeoutSec.label')}
                  value={String(draft.pictureDescriptionTimeoutSec)}
                  onChange={(next) => patchDraft({ pictureDescriptionTimeoutSec: Number(next) || 0 })}
                  disabled={!draft.pictureDescriptionEnabled}
                />
                <div className="md:col-span-2">
                  <TextareaField
                    label={t('systemSetup.forms.documentParsing.pictureDescriptionPrompt.label')}
                    value={draft.pictureDescriptionPrompt}
                    onChange={(pictureDescriptionPrompt) => patchDraft({ pictureDescriptionPrompt })}
                    placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionPrompt.placeholder')}
                    disabled={!draft.pictureDescriptionEnabled}
                    rows={4}
                  />
                </div>
                <div className="md:col-span-2">
                  <TextareaField
                    label={t('systemSetup.forms.documentParsing.pictureDescriptionParamsJson.label')}
                    value={draft.pictureDescriptionParamsJson}
                    onChange={(pictureDescriptionParamsJson) => patchDraft({ pictureDescriptionParamsJson })}
                    placeholder='{"temperature":0.2}'
                    disabled={!draft.pictureDescriptionEnabled}
                    rows={3}
                  />
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
