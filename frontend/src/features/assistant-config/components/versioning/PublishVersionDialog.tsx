import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

interface PublishVersionDialogProps {
  open: boolean
  submitting?: boolean
  defaultName: string
  onOpenChange: (open: boolean) => void
  onConfirm: (versionName: string) => void
}

export function PublishVersionDialog({
  open,
  submitting = false,
  defaultName,
  onOpenChange,
  onConfirm,
}: PublishVersionDialogProps) {
  const { t } = useTranslation()
  const [versionName, setVersionName] = useState(defaultName)

  useEffect(() => {
    if (!open) return
    setVersionName(defaultName)
  }, [defaultName, open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md rounded-[24px] border-white/80 bg-white/96 p-6 shadow-[0_24px_70px_rgba(15,23,42,0.22)]">
        <DialogHeader className="space-y-2 text-left">
          <DialogTitle>{t('settings.skills.versioning.publishDialogTitle')}</DialogTitle>
          <DialogDescription>
            {t('settings.skills.versioning.publishDialogSubtitle', {
              defaultValue: '保存当前草稿并创建一个新的发布版本。',
            })}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <label className="text-sm font-medium">
            {t('settings.skills.versioning.publishVersionName')}
          </label>
          <input
            value={versionName}
            onChange={(event) => setVersionName(event.target.value)}
            placeholder={t('settings.skills.versioning.publishVersionNamePlaceholder')}
            className="w-full rounded-md border border-input px-3 py-2 text-sm"
            disabled={submitting}
          />
        </div>
        <DialogFooter>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-50"
            disabled={submitting}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={() => onConfirm(versionName.trim())}
            className="rounded-xl bg-primary px-3 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
            disabled={submitting}
          >
            {t('settings.skills.workflowActions.saveAndPublish')}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
