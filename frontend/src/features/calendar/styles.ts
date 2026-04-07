import { uiChrome, uiRadius, uiSurface } from '@/components/ui/styles'

export const calendarRadius = {
  shell: uiRadius.shell,
  panel: uiRadius.panel,
  control: uiRadius.control,
  micro: uiRadius.inset,
  pill: uiRadius.pill,
  eventStart: 'rounded-l-[16px]',
  eventContinue: 'rounded-l-[12px]',
  eventEnd: 'rounded-r-[16px]',
} as const

export const calendarSurface = {
  routeBackdrop: uiSurface.pageBackdrop,
  shell: uiChrome.shell,
  panel: uiChrome.card,
  control: uiChrome.control,
  inset: uiChrome.inset,
  popover: uiChrome.float,
  dialog: uiChrome.modal,
} as const

export const calendarTone = {
  cell: 'bg-background/82 hover:bg-background/97',
  cellMuted:
    'bg-slate-50/65 text-muted-foreground dark:bg-white/[0.03] dark:text-muted-foreground',
  cellToday: 'bg-slate-50/92 dark:bg-white/[0.05]',
} as const
