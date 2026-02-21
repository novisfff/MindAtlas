import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

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
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('settings.skills.versioning.publishDialogTitle')}</DialogTitle>
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
            className="rounded-md border px-3 py-2 text-sm hover:bg-muted"
            disabled={submitting}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={() => onConfirm(versionName.trim())}
            className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            disabled={submitting}
          >
            {t('settings.skills.workflowActions.saveAndPublish')}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
