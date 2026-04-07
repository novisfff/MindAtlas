import { cn } from '@/lib/utils'

export const uiRadius = {
  shell: 'rounded-[24px]',
  panel: 'rounded-[20px]',
  control: 'rounded-[16px]',
  inset: 'rounded-[12px]',
  pill: 'rounded-full',
} as const

export const uiElevation = {
  base: 'shadow-[0_6px_16px_rgba(15,23,42,0.045)] dark:shadow-[0_10px_24px_rgba(2,6,23,0.22)]',
  raised:
    'shadow-[0_10px_28px_rgba(15,23,42,0.055)] dark:shadow-[0_16px_36px_rgba(2,6,23,0.26)]',
  float:
    'shadow-[0_18px_42px_rgba(15,23,42,0.14)] dark:shadow-[0_20px_46px_rgba(2,6,23,0.34)]',
  modal:
    'shadow-[0_24px_72px_rgba(15,23,42,0.16)] dark:shadow-[0_28px_72px_rgba(2,6,23,0.4)]',
} as const

export const uiSurface = {
  pageBackdrop:
    'bg-[radial-gradient(circle_at_top_left,rgba(226,232,240,0.26),transparent_30%),linear-gradient(180deg,rgba(255,255,255,0.998),rgba(248,250,252,0.986))] dark:bg-[radial-gradient(circle_at_top_left,rgba(71,85,105,0.16),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.985),rgba(15,23,42,0.965))]',
  shell: cn(
    'border border-slate-200/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.985),rgba(250,250,250,0.968))] dark:border-slate-800/80 dark:bg-[linear-gradient(180deg,rgba(17,24,39,0.97),rgba(15,23,42,0.955))]',
    uiElevation.raised,
  ),
  card: cn(
    'border border-slate-200/75 bg-background/94 dark:border-slate-800/80 dark:bg-background/96',
    uiElevation.base,
  ),
  control: cn(
    'border border-slate-200/80 bg-background/92 dark:border-slate-800/80 dark:bg-background/92',
    uiElevation.base,
  ),
  inset:
    'border border-slate-200/75 bg-slate-50/72 dark:border-slate-800/75 dark:bg-white/[0.04]',
  float: cn(
    'border border-slate-200/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.94),rgba(248,250,252,0.88))] backdrop-blur-md dark:border-slate-800/80 dark:bg-[linear-gradient(180deg,rgba(17,24,39,0.9),rgba(15,23,42,0.84))]',
    uiElevation.float,
  ),
  modal: cn(
    'border border-slate-200/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.975),rgba(250,250,250,0.92))] backdrop-blur-md dark:border-slate-800/80 dark:bg-[linear-gradient(180deg,rgba(17,24,39,0.95),rgba(15,23,42,0.88))]',
    uiElevation.modal,
  ),
  overlay: 'bg-slate-950/26 backdrop-blur-[3px] dark:bg-black/48',
  headerGlass:
    'border-b border-border/60 bg-background/92 backdrop-blur-md dark:bg-background/88',
  sidebar:
    'border-r border-border/70 bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(248,250,252,0.92))] dark:border-slate-800/70 dark:bg-[linear-gradient(180deg,rgba(15,23,42,0.98),rgba(15,23,42,0.95))]',
} as const

export const uiChrome = {
  shell: cn(uiRadius.shell, uiSurface.shell),
  card: cn(uiRadius.panel, uiSurface.card),
  control: cn(uiRadius.control, uiSurface.control),
  inset: cn(uiRadius.inset, uiSurface.inset),
  float: cn(uiRadius.panel, uiSurface.float),
  modal: cn(uiRadius.shell, uiSurface.modal),
} as const

export const uiField = {
  input: cn(
    uiRadius.control,
    uiSurface.control,
    'h-10 w-full px-4 text-sm text-foreground outline-none transition placeholder:text-muted-foreground focus-visible:border-primary/25 focus-visible:ring-[3px] focus-visible:ring-primary/10 disabled:cursor-not-allowed disabled:opacity-50',
  ),
  textarea: cn(
    uiRadius.control,
    uiSurface.control,
    'w-full px-4 py-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground focus-visible:border-primary/25 focus-visible:ring-[3px] focus-visible:ring-primary/10 disabled:cursor-not-allowed disabled:opacity-50',
  ),
  select: cn(
    uiRadius.control,
    uiSurface.control,
    'h-10 w-full appearance-none px-4 text-sm text-foreground outline-none transition focus-visible:border-primary/25 focus-visible:ring-[3px] focus-visible:ring-primary/10 disabled:cursor-not-allowed disabled:opacity-50',
  ),
} as const

export const uiLayout = {
  page7: 'mx-auto w-full max-w-7xl space-y-6',
  page6: 'mx-auto w-full max-w-6xl space-y-6',
  headerBlock: 'space-y-2',
  headerRow: 'flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between',
  headerTitle: 'text-3xl font-semibold tracking-tight text-foreground',
  headerSubtitle:
    'max-w-3xl text-sm leading-7 text-muted-foreground md:text-base',
  backLink:
    'inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground',
  sectionStack: 'space-y-6',
} as const
