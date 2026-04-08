import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { uiChrome, uiLayout, uiRadius } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

export function SettingsPageShell({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn(uiLayout.page6, 'min-h-0', className)}>{children}</div>
}

export function SettingsPageHeader({
  title,
  description,
  actions,
  backAction,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  backAction?: {
    label: ReactNode
    onClick: () => void
  }
  className?: string
}) {
  return (
    <div className={cn(uiLayout.headerRow, className)}>
      <div className="space-y-3">
        {backAction ? (
          <button type="button" onClick={backAction.onClick} className={uiLayout.backLink}>
            <ArrowLeft className="h-4 w-4" />
            {backAction.label}
          </button>
        ) : null}
        <div className={uiLayout.headerBlock}>
          <h1 className={uiLayout.headerTitle}>{title}</h1>
          {description ? <p className={uiLayout.headerSubtitle}>{description}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
    </div>
  )
}

export function SettingsSection({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <section className={cn(uiChrome.card, 'p-6', className)}>{children}</section>
}

export function SettingsSectionHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between', className)}>
      <div className="space-y-1.5">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {description ? (
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export function SettingsInset({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn(uiChrome.inset, 'p-4', className)}>{children}</div>
}

export function SettingsBadge({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        uiRadius.pill,
        'inline-flex items-center border border-border/70 bg-background/88 px-3 py-1 text-[11px] font-medium text-muted-foreground',
        className,
      )}
    >
      {children}
    </span>
  )
}

export function SettingsEmptyState({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        uiRadius.panel,
        'border border-dashed border-border/80 bg-muted/20 px-6 py-12 text-center',
        className,
      )}
    >
      <div className="mx-auto max-w-md space-y-2">
        <p className="text-sm font-medium text-foreground">{title}</p>
        {description ? <p className="text-sm leading-6 text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  )
}

export function SettingsWorkspace({
  sidebar,
  content,
  className,
}: {
  sidebar: ReactNode
  content: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn(
        uiChrome.shell,
        'grid min-h-[640px] min-w-0 grid-cols-1 overflow-hidden p-0 lg:grid-cols-[280px_minmax(0,1fr)]',
        className,
      )}
    >
      <aside className="min-h-0 border-b border-border/70 lg:border-b-0 lg:border-r">{sidebar}</aside>
      <div className="min-h-0 min-w-0">{content}</div>
    </section>
  )
}

export function SettingsWorkspaceSidebar({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('flex h-full min-h-0 flex-col', className)}>{children}</div>
}

export function SettingsWorkspaceContent({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('flex h-full min-h-0 flex-col bg-background/60', className)}>{children}</div>
}
