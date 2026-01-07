import { ChevronLeft, ChevronRight } from 'lucide-react'

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
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-center space-x-2 py-8">
      <button
        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 w-9"
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage <= 1}
        aria-label="Previous page"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>

      <div className="flex items-center gap-1 text-sm font-medium">
        <span className="px-2">
          Page {currentPage} of {totalPages}
        </span>
      </div>

      <button
        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 w-9"
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage >= totalPages}
        aria-label="Next page"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  )
}
