import { useEffect, useState } from 'react'
import { Clock3, Loader2, Save } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { uiChrome } from '@/components/ui/styles'
import {
  RuntimeCapabilityMeta,
  ToggleCard,
  useRuntimeConfigQuery,
  useUpdateRuntimeConfigMutation,
  type RuntimeAutomationConfigRequest,
  type RuntimeAutomationConfigResponse,
} from '@/features/system-setup'
import { SettingsPageHeader, SettingsPageShell } from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'

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
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.automation')}
        description={t('pages.settings.automationDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        actions={
          <Button
            type="button"
            onClick={() => {
              void handleSave()
            }}
            disabled={updateMutation.isPending || !isDirty}
          >
            {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {t('common.save')}
          </Button>
        }
      />

      <section className={cn(uiChrome.card, 'p-6')}>
        <div className="space-y-4">
          <RuntimeCapabilityMeta module={current} skipped={false} t={t} />
          <div className="space-y-2">
            <p className="text-sm font-semibold text-foreground">
              {t('systemSetup.detailPages.effectiveSummary')}
            </p>
            <p className="text-sm leading-6 text-muted-foreground">{current.effectiveSummary}</p>
          </div>
          <p className="text-sm leading-6 text-muted-foreground">
            {t('systemSetup.detailPages.automation.applyHint')}
          </p>
        </div>
      </section>

      <section className={cn(uiChrome.card, 'p-6')}>
        <div className="space-y-5">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-foreground">
              {t('systemSetup.detailPages.automation.sections.scheduler')}
            </h2>
            <p className="text-sm leading-6 text-muted-foreground">
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

      <section className={cn(uiChrome.card, 'p-6')}>
        <div className="space-y-5">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-foreground">
              {t('systemSetup.detailPages.automation.sections.jobs')}
            </h2>
            <p className="text-sm leading-6 text-muted-foreground">
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
              <div key={item.key} className={cn(uiChrome.inset, 'p-5')}>
                <div className="flex items-start gap-3">
                  <div className={cn(uiChrome.control, 'p-3 text-foreground shadow-none')}>
                    <Clock3 className="h-5 w-5" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-foreground">{item.title}</p>
                    <p className="text-sm leading-6 text-muted-foreground">{item.schedule}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className={cn(uiChrome.inset, 'px-4 py-4 text-sm leading-6 text-muted-foreground')}>
            {t('systemSetup.detailPages.automation.manualHint')}
          </div>
        </div>
      </section>
    </SettingsPageShell>
  )
}
