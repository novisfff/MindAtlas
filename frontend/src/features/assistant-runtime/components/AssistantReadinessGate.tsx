import type { ReactNode } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { usePublicAssistantReadinessQuery } from '../queries'
import type { AssistantReadinessReason } from '../api/runtime'
import { isIntegrityStopReason, reasonTranslationKey } from './reasonCopy'

function AssistantReadinessSkeleton() {
  const { t } = useTranslation()
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 px-6 py-10 text-center"
      data-testid="assistant-readiness-skeleton"
      role="status"
    >
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">
          {t('assistantRuntime.readiness.loadingTitle')}
        </p>
        <p className="text-xs text-muted-foreground">
          {t('assistantRuntime.readiness.loadingDescription')}
        </p>
      </div>
    </div>
  )
}

function AssistantUnavailablePanel({
  reasonCodes,
}: {
  reasonCodes: AssistantReadinessReason[]
}) {
  const { t } = useTranslation()
  return (
    <div
      className="mx-auto flex max-w-xl flex-col gap-4 rounded-[24px] border border-border/80 bg-background/90 px-5 py-6 shadow-sm"
      data-testid="assistant-unavailable-panel"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <div className="rounded-2xl bg-amber-50 p-3 text-amber-700">
          <AlertCircle className="h-5 w-5" />
        </div>
        <div className="space-y-1">
          <h2 className="text-base font-semibold text-foreground">
            {t('assistantRuntime.readiness.unavailableTitle')}
          </h2>
          <p className="text-sm leading-6 text-muted-foreground">
            {t('assistantRuntime.readiness.unavailableDescription')}
          </p>
        </div>
      </div>
      {reasonCodes.length > 0 ? (
        <ul className="space-y-2">
          {reasonCodes.map((code) => (
            <li
              key={code}
              className="rounded-xl border border-border/70 bg-muted/40 px-3 py-2 text-sm text-foreground"
            >
              {t(reasonTranslationKey(code), { defaultValue: code })}
              {isIntegrityStopReason(code) ? null : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

export function AssistantReadinessGate({ children }: { children: ReactNode }) {
  const readiness = usePublicAssistantReadinessQuery()

  if (readiness.isLoading && !readiness.data) {
    return <AssistantReadinessSkeleton />
  }

  if (!readiness.data?.ready) {
    return (
      <AssistantUnavailablePanel
        reasonCodes={(readiness.data?.reasonCodes ?? []) as AssistantReadinessReason[]}
      />
    )
  }

  return <>{children}</>
}

export { AssistantReadinessSkeleton, AssistantUnavailablePanel }
