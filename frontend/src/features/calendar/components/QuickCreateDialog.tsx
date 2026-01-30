import { useState } from 'react'
import { format } from 'date-fns'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface QuickCreateDialogProps {
  date: Date
  isOpen: boolean
  onClose: () => void
}

export function QuickCreateDialog({
  date,
  isOpen,
  onClose,
}: QuickCreateDialogProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()

  if (!isOpen) return null

  const handleCreate = () => {
    const dateStr = format(date, 'yyyy-MM-dd')
    navigate(`/entries/new?date=${dateStr}`)
    onClose()
  }

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/50" onClick={onClose} />
      <div className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2">
        <div className="w-80 rounded-lg border bg-background p-4 shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold">
              {format(date, 'yyyy-MM-dd')}
            </h3>
            <button onClick={onClose} className="p-1 hover:bg-muted rounded">
              <X className="w-4 h-4" />
            </button>
          </div>
          <button
            onClick={handleCreate}
            className={cn(
              'w-full py-2 rounded-lg',
              'bg-primary text-primary-foreground',
              'hover:bg-primary/90'
            )}
          >
            {t('actions.newEntry')}
          </button>
        </div>
      </div>
    </>
  )
}
