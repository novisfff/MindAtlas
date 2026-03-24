import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type ResetDangerMode = 'single' | 'all'
type ResetDangerScope = 'skills' | 'systemBehaviors'

interface ResetDangerConfirmDialogProps {
  open: boolean
  mode: ResetDangerMode
  scope?: ResetDangerScope
  targetName?: string | null
  affectedCount?: number
  loading?: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

const RESET_PHRASE = 'RESET'

export function ResetDangerConfirmDialog({
  open,
  mode,
  scope = 'skills',
  targetName,
  affectedCount = 0,
  loading = false,
  onOpenChange,
  onConfirm,
}: ResetDangerConfirmDialogProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState<1 | 2>(1)
  const [typedValue, setTypedValue] = useState('')

  useEffect(() => {
    if (!open) {
      setStep(1)
      setTypedValue('')
      return
    }
    setStep(1)
    setTypedValue('')
  }, [open, mode, targetName])

  const isResetAll = mode === 'all'
  const keyPrefix = scope === 'systemBehaviors' ? 'settings.systemBehaviors' : 'settings.skills'
  const finalConfirmLabel = isResetAll
    ? t(`${keyPrefix}.resetAllFinalConfirm`)
    : t(`${keyPrefix}.resetFinalConfirm`)

  const warningDescription = useMemo(() => {
    if (isResetAll) {
      return t(`${keyPrefix}.resetAllWarningDescription`, { count: affectedCount })
    }
    return t(`${keyPrefix}.resetWarningDescription`, { name: targetName || '-' })
  }, [affectedCount, isResetAll, keyPrefix, t, targetName])

  const canSubmit = typedValue === RESET_PHRASE && !loading

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => (!loading ? onOpenChange(nextOpen) : undefined)}>
      <DialogContent className="max-w-xl overflow-hidden p-0">
        <div className="border-b bg-gradient-to-r from-red-500/12 via-orange-500/12 to-amber-500/12 px-6 py-4">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base sm:text-lg">
              <AlertTriangle className="h-5 w-5 text-orange-600" />
              {isResetAll ? t(`${keyPrefix}.resetAllWarningTitle`) : t(`${keyPrefix}.resetWarningTitle`)}
            </DialogTitle>
          </DialogHeader>
        </div>

        <div className="space-y-4 px-6 pb-6 pt-4">
          <p className="text-sm text-muted-foreground">{warningDescription}</p>

          <div className="rounded-xl border border-orange-200/70 bg-orange-50/70 p-4">
            <div className="space-y-2 text-sm">
              <p className="font-medium text-orange-700">{t(`${keyPrefix}.resetWarningRebindSystemTarget`)}</p>
              <p className="text-muted-foreground">{t(`${keyPrefix}.resetWarningNoUserTargetMutation`)}</p>
              <p className="text-muted-foreground">{t(`${keyPrefix}.resetWarningVersionCleanup`)}</p>
            </div>
          </div>

          {step === 2 ? (
            <div className="space-y-2 rounded-xl border bg-muted/25 p-4">
              <label className="text-sm font-medium" htmlFor="reset-confirm-input">
                {t(`${keyPrefix}.resetTypeLabel`)}
              </label>
              <input
                id="reset-confirm-input"
                value={typedValue}
                onChange={(event) => setTypedValue(event.target.value.trim())}
                placeholder={t(`${keyPrefix}.resetTypePlaceholder`)}
                className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                disabled={loading}
                autoFocus
              />
              {typedValue.length > 0 && typedValue !== RESET_PHRASE ? (
                <p className="text-xs text-red-600">{t(`${keyPrefix}.resetTypeMismatch`)}</p>
              ) : null}
            </div>
          ) : null}
        </div>

        <DialogFooter className="border-t bg-muted/10 px-6 py-4">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-md border px-3 py-2 text-sm hover:bg-muted"
            disabled={loading}
          >
            {t('common.cancel')}
          </button>
          {step === 1 ? (
            <button
              type="button"
              onClick={() => setStep(2)}
              className="rounded-md bg-orange-600 px-3 py-2 text-sm text-white hover:bg-orange-700"
              disabled={loading}
            >
              {t(`${keyPrefix}.resetProceed`)}
            </button>
          ) : (
            <button
              type="button"
              onClick={onConfirm}
              className="inline-flex items-center justify-center rounded-md bg-red-600 px-3 py-2 text-sm text-white hover:bg-red-700 disabled:opacity-50"
              disabled={!canSubmit}
            >
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {finalConfirmLabel}
            </button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
