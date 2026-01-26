import { Clock, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { IndexStatus } from '../api/entries'

interface IndexStatusBadgeProps {
  status: IndexStatus['status']
}

export function IndexStatusBadge({ status }: IndexStatusBadgeProps) {
  const { t } = useTranslation()

  const configs = {
    pending: { icon: Clock, color: 'text-yellow-600 bg-yellow-50 border-yellow-200' },
    processing: { icon: Loader2, color: 'text-blue-600 bg-blue-50 border-blue-200', spin: true },
    succeeded: { icon: CheckCircle2, color: 'text-green-600 bg-green-50 border-green-200' },
    dead: { icon: XCircle, color: 'text-red-600 bg-red-50 border-red-200' },
    unknown: { icon: Clock, color: 'text-gray-500 bg-gray-50 border-gray-200' },
  }

  const config = configs[status] || configs.unknown
  const Icon = config.icon

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
        config.color
      )}
    >
      <Icon className={cn('w-3 h-3', (config as any).spin && 'animate-spin')} />
      {t(`indexStatus.${status}`)}
    </span>
  )
}
