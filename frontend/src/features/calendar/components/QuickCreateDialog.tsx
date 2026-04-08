import { format } from 'date-fns'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { calendarRadius, calendarSurface } from '../styles'

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
      <div
        className="fixed inset-0 z-50 bg-slate-950/28 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2">
        <div
          className={cn(
            'w-80 p-4',
            calendarRadius.shell,
            calendarSurface.dialog,
          )}
        >
          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-semibold tracking-tight text-foreground">
              {format(date, 'yyyy-MM-dd')}
            </h3>
            <button
              type="button"
              onClick={onClose}
              className={cn(
                'p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground',
                calendarRadius.micro,
              )}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            className={cn(
              'w-full py-2 font-medium',
              calendarRadius.control,
              'bg-primary text-primary-foreground',
              'hover:bg-primary/90',
            )}
          >
            {t('actions.newEntry')}
          </button>
        </div>
      </div>
    </>
  )
}
