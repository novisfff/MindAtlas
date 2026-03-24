import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

type WorkflowEditorSurfaceSize = 'narrow' | 'default' | 'wide' | 'full'
type WorkflowEditorSurfaceDensity = 'default' | 'compact'

interface WorkflowEditorSurfaceShellProps {
  title: ReactNode
  subtitle?: ReactNode
  icon?: ReactNode
  size?: WorkflowEditorSurfaceSize
  density?: WorkflowEditorSurfaceDensity
  fluid?: boolean
  onClose?: () => void
  headerActions?: ReactNode
  footer?: ReactNode
  className?: string
  headerClassName?: string
  bodyClassName?: string
  footerClassName?: string
  children: ReactNode
}

const SIZE_CLASS_MAP: Record<WorkflowEditorSurfaceSize, string> = {
  narrow: 'w-[420px] max-w-[calc(100vw-2rem)]',
  default: 'w-[460px] max-w-[calc(100vw-2rem)]',
  wide: 'w-[520px] max-w-[calc(100vw-2rem)]',
  full: 'w-full max-w-none',
}

export function WorkflowEditorSurfaceShell({
  title,
  subtitle,
  icon,
  size = 'default',
  density = 'default',
  fluid = false,
  onClose,
  headerActions,
  footer,
  className,
  headerClassName,
  bodyClassName,
  footerClassName,
  children,
}: WorkflowEditorSurfaceShellProps) {
  const { t } = useTranslation()
  const isCompact = density === 'compact'

  return (
    <section
      className={cn(
        'pointer-events-auto flex h-full min-h-0 flex-col overflow-hidden border border-white/70 bg-white/95 backdrop-blur-xl',
        isCompact
          ? 'rounded-[22px] shadow-[0_14px_40px_rgba(15,23,42,0.12)]'
          : 'rounded-[28px] shadow-[0_22px_70px_rgba(15,23,42,0.16)]',
        fluid ? 'w-full max-w-none' : SIZE_CLASS_MAP[size],
        className,
      )}
    >
      <header
        className={cn(
          'flex shrink-0 items-start justify-between border-b border-slate-200/80 bg-white/92',
          isCompact ? 'gap-3 px-3.5 py-3' : 'gap-4 px-5 py-4',
          headerClassName,
        )}
      >
        <div className={cn('flex min-w-0 flex-1 items-start', isCompact ? 'gap-2.5' : 'gap-3')}>
          {icon ? (
            <div className={cn(
              'mt-0.5 flex shrink-0 items-center justify-center border border-blue-200 bg-blue-50 text-blue-700 shadow-sm',
              isCompact ? 'h-8 w-8 rounded-xl' : 'h-11 w-11 rounded-2xl',
            )}>
              {icon}
            </div>
          ) : null}
          <div className="min-w-0 flex-1">
            {typeof title === 'string' ? (
              <div className={cn('truncate font-semibold text-slate-900', isCompact ? 'text-base leading-6' : 'text-lg')}>
                {title}
              </div>
            ) : (
              title
            )}
            {subtitle ? (
              <div className={cn('text-slate-500', isCompact ? 'mt-0.5 text-xs leading-[18px]' : 'mt-1 text-sm leading-6')}>
                {subtitle}
              </div>
            ) : null}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {headerActions}
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className={cn(
                'inline-flex items-center justify-center border border-transparent text-slate-500 transition-colors hover:border-slate-200 hover:bg-slate-100 hover:text-slate-800',
                isCompact ? 'h-8 w-8 rounded-xl' : 'h-10 w-10 rounded-2xl',
              )}
              title={t('actions.close')}
              aria-label={t('actions.close')}
            >
              <X className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </header>

      <div className={cn('min-h-0 flex-1', bodyClassName)}>
        {children}
      </div>

      {footer ? (
        <footer className={cn(
          'shrink-0 border-t border-slate-200/80 bg-white/96',
          isCompact ? 'px-3.5 py-3' : 'px-5 py-4',
          footerClassName,
        )}>
          {footer}
        </footer>
      ) : null}
    </section>
  )
}
