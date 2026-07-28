import { useEffect, useRef, type ReactNode } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Logo } from '@/components/Logo'
import { SESSION_EXPIRED_EVENT } from '@/lib/api/client'
import { useInitializationStatusQuery, initializationKeys } from '@/features/initialization/queries'
import { operatorSessionKeys, useOperatorSessionQuery } from '../queries'

function FullScreenState({ title, description }: { title: string; description: string }) {
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
      </div>
    </div>
  )
}

function isProtectedQueryKey(queryKey: readonly unknown[]): boolean {
  const root = queryKey[0]
  if (root === operatorSessionKeys.session[0]) return false
  if (root === initializationKeys.status[0]) return false
  return true
}

export function OperatorGate({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const initStatus = useInitializationStatusQuery()
  const isInitializationRoute = location.pathname.startsWith('/initialize')
  const isLoginRoute = location.pathname === '/login'
  const systemInitialized = Boolean(initStatus.data?.initialized)
  const authRequired = systemInitialized && !isInitializationRoute
  const sessionQuery = useOperatorSessionQuery(authRequired)
  const handlingExpiryRef = useRef(false)

  useEffect(() => {
    const onSessionExpired = () => {
      if (handlingExpiryRef.current) return
      if (location.pathname === '/login' || location.pathname.startsWith('/initialize')) return
      if (!systemInitialized) return

      handlingExpiryRef.current = true
      queryClient.setQueryData(operatorSessionKeys.session, { authenticated: false })
      queryClient.removeQueries({
        predicate: (query) => isProtectedQueryKey(query.queryKey),
      })
      void queryClient.invalidateQueries({ queryKey: operatorSessionKeys.session })
      toast.error(t('operatorAuth.gate.sessionExpired'))
      navigate('/login', { replace: true, state: { from: location.pathname } })
      window.setTimeout(() => {
        handlingExpiryRef.current = false
      }, 500)
    }

    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
    }
  }, [location.pathname, navigate, queryClient, systemInitialized, t])

  // Uninitialized systems and the initialization wizard itself never require a session.
  if (!systemInitialized || isInitializationRoute) {
    return <>{children}</>
  }

  if (sessionQuery.isLoading && !sessionQuery.data) {
    return (
      <FullScreenState
        title={t('operatorAuth.gate.loadingTitle')}
        description={t('operatorAuth.gate.loadingDescription')}
      />
    )
  }

  const authenticated = Boolean(sessionQuery.data?.authenticated)

  if (!authenticated && !isLoginRoute) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  if (authenticated && isLoginRoute) {
    return <Navigate to="/dashboard" replace />
  }

  return <>{children}</>
}
