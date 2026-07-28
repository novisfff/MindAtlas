import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Loader2, LockKeyhole } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { ApiError, isApiError } from '@/lib/api/client'
import { operatorSessionKeys, useOperatorLoginMutation } from '../queries'

function resolveLoginErrorMessage(error: unknown, t: (key: string) => string): string {
  if (isApiError(error)) {
    if (error.status === 429 || error.code === 42910) {
      return t('operatorAuth.login.errors.locked')
    }
    if (error.status === 503 || error.code === 50310) {
      return t('operatorAuth.login.errors.unavailable')
    }
    if (error.status === 401 || error.code === 40111) {
      return t('operatorAuth.login.errors.invalid')
    }
  }
  if (error instanceof ApiError) {
    return t('operatorAuth.login.errors.generic')
  }
  return t('operatorAuth.login.errors.generic')
}

export function OperatorLoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const loginMutation = useOperatorLoginMutation()
  const [password, setPassword] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setErrorMessage(null)

    // Passwords are exact Unicode strings — never trim or normalize.
    const exactPassword = password

    try {
      const session = await loginMutation.mutateAsync(exactPassword)
      queryClient.setQueryData(operatorSessionKeys.session, session)
      navigate('/dashboard', { replace: true })
    } catch (error) {
      setErrorMessage(resolveLoginErrorMessage(error, t))
    } finally {
      // Clear UI state and RQ mutation variables so the password does not linger.
      setPassword('')
      loginMutation.reset()
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.16),_transparent_42%),linear-gradient(180deg,_#f8fbff,_#f5f7fb_52%,_#eef4ff)] px-6">
      <div className="w-full max-w-md rounded-[28px] border border-slate-200/80 bg-white/90 px-8 py-10 shadow-[0_28px_80px_rgba(15,23,42,0.14)] backdrop-blur">
        <div className="flex flex-col items-center gap-4 text-center">
          <div className="rounded-2xl bg-slate-900 p-3 text-white">
            <Logo className="h-8 w-8" />
          </div>
          <div className="space-y-2">
            <h1 className="text-xl font-semibold text-slate-900">
              {t('operatorAuth.login.title')}
            </h1>
            <p className="text-sm leading-6 text-slate-600">
              {t('operatorAuth.login.description')}
            </p>
          </div>
        </div>

        <form className="mt-8 space-y-5" onSubmit={(event) => void handleSubmit(event)} noValidate>
          <div className="space-y-2 text-left">
            <label htmlFor="operator-password" className="text-sm font-medium text-slate-800">
              {t('operatorAuth.login.passwordLabel')}
            </label>
            <div className="relative">
              <LockKeyhole className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                id="operator-password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-11 w-full rounded-2xl border border-slate-200 bg-white pl-10 pr-4 text-sm text-slate-900 shadow-sm outline-none transition focus:border-slate-900/50 focus:ring-4 focus:ring-slate-900/5"
                disabled={loginMutation.isPending}
              />
            </div>
          </div>

          {errorMessage ? (
            <p role="alert" className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
              {errorMessage}
            </p>
          ) : null}

          <Button
            type="submit"
            className="w-full rounded-2xl"
            disabled={loginMutation.isPending || password.length === 0}
          >
            {loginMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {t('operatorAuth.login.submitting')}
              </>
            ) : (
              t('operatorAuth.login.submit')
            )}
          </Button>
        </form>
      </div>
    </div>
  )
}
