import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

interface EntriesPaginationProps {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
}

export function EntriesPagination({
  currentPage,
  totalPages,
  onPageChange,
}: EntriesPaginationProps) {
  const { t } = useTranslation()

  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-center space-x-2 py-8">
      <Button
        variant="outline"
        size="icon"
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage <= 1}
        aria-label={t('actions.previousPage')}
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>

      <div className="flex items-center gap-1 text-sm font-medium">
        <span className="px-2">
          {t('common.pageOf', { current: currentPage, total: totalPages })}
        </span>
      </div>

      <Button
        variant="outline"
        size="icon"
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage >= totalPages}
        aria-label={t('actions.nextPage')}
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  )
}
