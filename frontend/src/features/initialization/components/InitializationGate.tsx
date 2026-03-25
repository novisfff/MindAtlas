import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { AlertCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { useInitializationStatusQuery } from '../queries'

function FullScreenState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.16),_transparent_42%),linear-gradient(180deg,_#f8fbff,_#f5f7fb_52%,_#eef4ff)] px-6">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-[28px] border border-slate-200/80 bg-white/90 px-8 py-10 text-center shadow-[0_28px_80px_rgba(15,23,42,0.14)] backdrop-blur">
        <div className="rounded-2xl bg-slate-900 p-3 text-white">
          <Logo className="h-8 w-8" />
        </div>
        <div className="space-y-2">
          <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
          <p className="text-sm leading-6 text-slate-600">{description}</p>
        </div>
        {action}
      </div>
    </div>
  )
}

export function InitializationGate({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { t } = useTranslation()
  const statusQuery = useInitializationStatusQuery()
  const isInitializationRoute = location.pathname.startsWith('/initialize')

  if (statusQuery.isLoading && !statusQuery.data && !isInitializationRoute) {
    return (
      <FullScreenState
        title={t('initialization.loadingTitle')}
        description={t('initialization.loadingDescription')}
      />
    )
  }

  if ((statusQuery.isError || !statusQuery.data) && !isInitializationRoute) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
        <div className="max-w-md rounded-[28px] border border-red-100 bg-white p-8 shadow-sm">
          <div className="flex items-start gap-3">
            <div className="rounded-2xl bg-red-50 p-3 text-red-600">
              <AlertCircle className="h-6 w-6" />
            </div>
            <div className="space-y-3">
              <h1 className="text-lg font-semibold text-slate-900">
                {t('initialization.statusErrorTitle')}
              </h1>
              <p className="text-sm leading-6 text-slate-600">
                {t('initialization.statusErrorDescription')}
              </p>
              <Button onClick={() => statusQuery.refetch()}>
                {t('initialization.retry')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (!statusQuery.data) {
    return <>{children}</>
  }

  if (!statusQuery.data.initialized && !isInitializationRoute) {
    return <Navigate to="/initialize" replace />
  }

  if (statusQuery.data.initialized && isInitializationRoute) {
    return <Navigate to="/dashboard" replace />
  }

  return <>{children}</>
}
