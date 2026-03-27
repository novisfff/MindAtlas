import { useEffect, useState } from 'react'
import { ArrowLeft, Clock3, Loader2, Save } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  RuntimeCapabilityMeta,
  ToggleCard,
  useRuntimeConfigQuery,
  useUpdateRuntimeConfigMutation,
  type RuntimeAutomationConfigRequest,
  type RuntimeAutomationConfigResponse,
} from '@/features/system-setup'

interface AutomationDraft extends RuntimeAutomationConfigResponse {}

function createDraft(value: RuntimeAutomationConfigResponse): AutomationDraft {
  return {
    ...value,
  }
}

export function AutomationSettingsPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const runtimeConfigQuery = useRuntimeConfigQuery()
  const updateMutation = useUpdateRuntimeConfigMutation('automation')
  const [draft, setDraft] = useState<AutomationDraft | null>(null)
  const [isDirty, setIsDirty] = useState(false)

  const current = runtimeConfigQuery.data?.automation ?? null

  useEffect(() => {
    if (!current) return
    setDraft((existing) => (existing && isDirty ? existing : createDraft(current)))
  }, [current, isDirty])

  const patchDraft = (patch: Partial<AutomationDraft>) => {
    setDraft((currentDraft) => (currentDraft ? { ...currentDraft, ...patch } : currentDraft))
    setIsDirty(true)
  }

  const buildPayload = (): RuntimeAutomationConfigRequest | null => {
    if (!draft) return null
    return {
      schedulerEnabled: draft.schedulerEnabled,
    }
  }

  const handleSave = async () => {
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
              {t('pages.settings.automation')}
            </h1>
            <p className="max-w-3xl text-sm leading-7 text-slate-600">
              {t('pages.settings.automationDesc')}
            </p>
          </div>
        </div>

        <Button
          type="button"
          onClick={() => {
            void handleSave()
          }}
          disabled={updateMutation.isPending || !isDirty}
          className="rounded-2xl"
        >
          {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {t('common.save')}
        </Button>
      </div>

      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-4">
          <RuntimeCapabilityMeta module={current} skipped={false} t={t} />
          <div className="space-y-2">
            <p className="text-sm font-semibold text-slate-900">
              {t('systemSetup.detailPages.effectiveSummary')}
            </p>
            <p className="text-sm leading-6 text-slate-600">{current.effectiveSummary}</p>
          </div>
          <p className="text-sm leading-6 text-slate-600">
            {t('systemSetup.detailPages.automation.applyHint')}
          </p>
        </div>
      </section>

      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-5">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-slate-900">
              {t('systemSetup.detailPages.automation.sections.scheduler')}
            </h2>
            <p className="text-sm leading-6 text-slate-600">
              {t('systemSetup.moduleDescriptions.automation')}
            </p>
          </div>

          <ToggleCard
            label={t('systemSetup.forms.automation.schedulerEnabled.label')}
            description={t('systemSetup.forms.automation.schedulerEnabled.description')}
            checked={draft.schedulerEnabled}
            onCheckedChange={(schedulerEnabled) => patchDraft({ schedulerEnabled })}
          />
        </div>
      </section>

      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-5">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-slate-900">
              {t('systemSetup.detailPages.automation.sections.jobs')}
            </h2>
            <p className="text-sm leading-6 text-slate-600">
              {t('systemSetup.detailPages.automation.jobsDescription')}
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {[
              {
                key: 'weekly',
                title: t('systemSetup.detailPages.automation.jobs.weekly.title'),
                schedule: t('systemSetup.detailPages.automation.jobs.weekly.schedule'),
              },
              {
                key: 'monthly',
                title: t('systemSetup.detailPages.automation.jobs.monthly.title'),
                schedule: t('systemSetup.detailPages.automation.jobs.monthly.schedule'),
              },
            ].map((item) => (
              <div key={item.key} className="rounded-[24px] border border-slate-200 bg-slate-50/60 p-5">
                <div className="flex items-start gap-3">
                  <div className="rounded-2xl bg-white p-3 text-slate-700 shadow-sm">
                    <Clock3 className="h-5 w-5" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                    <p className="text-sm leading-6 text-slate-600">{item.schedule}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-[24px] border border-slate-200 bg-slate-50/60 px-4 py-4 text-sm leading-6 text-slate-600">
            {t('systemSetup.detailPages.automation.manualHint')}
          </div>
        </div>
      </section>
    </div>
  )
}
