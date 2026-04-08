import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { CalendarEvent } from './CalendarEvent'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'
import type { CalendarDensity } from '../types'
import { calendarRadius, calendarSurface } from '../styles'

const VIEWPORT_MARGIN = 12
const DESKTOP_ASSISTANT_SAFE_INSET = 104
const POPOVER_CHROME_OFFSET = 68

type PopoverPlacement = 'top' | 'bottom'
type PopoverAlign = 'start' | 'center' | 'end'

interface MoreEventsPopoverProps {
  date: Date
  density: CalendarDensity
  entries: Entry[]
  visibleCount: number
  onEntryClick?: (entry: Entry) => void
}

export function MoreEventsPopover({
  date,
  density,
  entries,
  visibleCount,
  onEntryClick,
}: MoreEventsPopoverProps) {
  const [isOpen, setIsOpen] = useState(false)
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const hiddenCount = entries.length - visibleCount
  const moreLabel = t('calendar.viewMore', 'View more')
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const [placement, setPlacement] = useState<PopoverPlacement>('bottom')
  const [align, setAlign] = useState<PopoverAlign>('start')
  const [position, setPosition] = useState<{
    top: number
    left: number
    maxBodyHeight: number
  }>({
    top: -9999,
    left: -9999,
    maxBodyHeight: 224,
  })

  const title = useMemo(() => format(date, 'PP', { locale }), [date, locale])

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    const popover = popoverRef.current
    if (!trigger || !popover) return

    const gap = density === 'compact' ? 6 : 8
    const triggerRect = trigger.getBoundingClientRect()
    const popoverRect = popover.getBoundingClientRect()
    const rightSafeInset =
      window.innerWidth >= 1024
        ? DESKTOP_ASSISTANT_SAFE_INSET
        : VIEWPORT_MARGIN
    const maxLeft = Math.max(
      VIEWPORT_MARGIN,
      window.innerWidth - rightSafeInset - popoverRect.width,
    )

    const candidates: Array<{ align: PopoverAlign; rawLeft: number }> = [
      { align: 'start', rawLeft: triggerRect.left },
      {
        align: 'end',
        rawLeft: triggerRect.right - popoverRect.width,
      },
      {
        align: 'center',
        rawLeft: triggerRect.left + triggerRect.width / 2 - popoverRect.width / 2,
      },
    ]

    const bestCandidate = candidates.reduce<{
      align: PopoverAlign
      left: number
      shift: number
    } | null>((best, candidate) => {
      const left = Math.max(
        VIEWPORT_MARGIN,
        Math.min(candidate.rawLeft, maxLeft),
      )
      const shift = Math.abs(left - candidate.rawLeft)

      if (!best || shift < best.shift) {
        return { align: candidate.align, left, shift }
      }
      return best
    }, null)

    if (!bestCandidate) return

    const availableBelow =
      window.innerHeight - VIEWPORT_MARGIN - triggerRect.bottom - gap
    const availableAbove = triggerRect.top - VIEWPORT_MARGIN - gap

    let nextPlacement: PopoverPlacement = 'bottom'
    if (
      availableBelow < popoverRect.height &&
      availableAbove > availableBelow
    ) {
      nextPlacement = 'top'
    }

    const availableHeight =
      nextPlacement === 'bottom' ? availableBelow : availableAbove
    const maxBodyHeight = Math.max(
      132,
      Math.min(availableHeight - POPOVER_CHROME_OFFSET, 320),
    )

    let top =
      nextPlacement === 'bottom'
        ? triggerRect.bottom + gap
        : triggerRect.top - popoverRect.height - gap

    top = Math.max(
      VIEWPORT_MARGIN,
      Math.min(top, window.innerHeight - VIEWPORT_MARGIN - popoverRect.height),
    )

    setPlacement(nextPlacement)
    setAlign(bestCandidate.align)
    setPosition({
      top,
      left: bestCandidate.left,
      maxBodyHeight,
    })
  }, [density])

  useLayoutEffect(() => {
    if (!isOpen) return
    updatePosition()
    const raf = window.requestAnimationFrame(updatePosition)

    const onResize = () => updatePosition()
    const onScroll = () => updatePosition()
    window.addEventListener('resize', onResize)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      window.cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [isOpen, updatePosition, entries.length, title])

  if (hiddenCount <= 0) return null

  const originClass =
    placement === 'bottom'
      ? align === 'end'
        ? 'origin-top-right'
        : align === 'center'
          ? 'origin-top'
          : 'origin-top-left'
      : align === 'end'
        ? 'origin-bottom-right'
        : align === 'center'
          ? 'origin-bottom'
          : 'origin-bottom-left'

  return (
    <>
      <div className="relative flex h-full w-full items-start justify-start">
        <button
          ref={triggerRef}
          type="button"
          aria-label={t('calendar.moreEvents', { count: hiddenCount })}
          title={t('calendar.moreEvents', { count: hiddenCount })}
          onClick={() => setIsOpen(!isOpen)}
          className={cn(
            'flex shrink-0 items-center gap-1 self-start px-1.5 align-top font-medium leading-none text-muted-foreground transition-colors hover:bg-background hover:text-foreground',
            calendarRadius.micro,
            calendarSurface.control,
            density === 'compact' ? 'h-[15px] text-[10px]' : 'h-4 text-[10px]',
          )}
        >
          <span>{moreLabel}</span>
          <span
            className={cn(
              'inline-flex items-center justify-center bg-muted/65 font-semibold text-muted-foreground/85',
              calendarRadius.pill,
              density === 'compact'
                ? 'min-w-[15px] px-1 text-[9px]'
                : 'min-w-[16px] px-1 text-[9px]',
            )}
          >
            {hiddenCount}
          </span>
        </button>
      </div>

      {isOpen &&
        typeof document !== 'undefined' &&
        createPortal(
          <>
            <div
              className="fixed inset-0 z-40"
              onClick={() => setIsOpen(false)}
            />
            <div
              ref={popoverRef}
              className={cn(
                'fixed z-50 w-72 max-w-[calc(100vw-16px)] p-3.5',
                calendarRadius.panel,
                calendarSurface.popover,
                originClass,
              )}
              style={{ top: position.top, left: position.left }}
            >
              <div className="mb-2.5 text-sm font-semibold tracking-tight">
                {title}
              </div>
              <div
                className="custom-scrollbar space-y-1.5 overflow-auto"
                style={{ maxHeight: `${position.maxBodyHeight}px` }}
              >
                {entries.map((entry) => (
                  <CalendarEvent
                    key={entry.id}
                    entry={entry}
                    density={density}
                    onClick={() => {
                      setIsOpen(false)
                      onEntryClick?.(entry)
                    }}
                  />
                ))}
              </div>
            </div>
          </>,
          document.body,
        )}
    </>
  )
}
