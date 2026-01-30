import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { CalendarEvent } from './CalendarEvent'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface MoreEventsPopoverProps {
  date: Date
  entries: Entry[]
  visibleCount: number
  onEntryClick?: (entry: Entry) => void
}

export function MoreEventsPopover({
  date,
  entries,
  visibleCount,
  onEntryClick,
}: MoreEventsPopoverProps) {
  const [isOpen, setIsOpen] = useState(false)
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const hiddenCount = entries.length - visibleCount
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const [placement, setPlacement] = useState<'top' | 'bottom'>('bottom')
  const [position, setPosition] = useState<{ top: number; left: number }>({ top: 0, left: 0 })

  const title = useMemo(() => format(date, 'MMMM d', { locale }), [date, locale])

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    const popover = popoverRef.current
    if (!trigger || !popover) return

    const margin = 8
    const triggerRect = trigger.getBoundingClientRect()
    const popoverRect = popover.getBoundingClientRect()

    // Prefer below the trigger; flip above if it would overflow.
    let nextPlacement: 'top' | 'bottom' = 'bottom'
    let top = triggerRect.bottom + margin
    if (top + popoverRect.height + margin > window.innerHeight) {
      nextPlacement = 'top'
      top = triggerRect.top - popoverRect.height - margin
    }

    // Clamp within viewport
    top = Math.max(margin, Math.min(top, window.innerHeight - popoverRect.height - margin))

    // Align left with trigger; clamp within viewport
    let left = triggerRect.left
    left = Math.max(margin, Math.min(left, window.innerWidth - popoverRect.width - margin))

    setPlacement(nextPlacement)
    setPosition({ top, left })
  }, [])

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

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        onClick={() => setIsOpen(!isOpen)}
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        {t('calendar.moreEvents', { count: hiddenCount })}
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          <div
            ref={popoverRef}
            className={cn(
              'fixed z-50 w-64 max-w-[calc(100vw-16px)] rounded-lg border bg-background p-3 shadow-lg',
              placement === 'bottom' ? 'origin-top-left' : 'origin-bottom-left'
            )}
            style={{ top: position.top, left: position.left }}
          >
            <div className="mb-2 font-medium">
              {title}
            </div>
            <div className="space-y-1 max-h-48 overflow-auto">
              {entries.map((entry) => (
                <CalendarEvent
                  key={entry.id}
                  entry={entry}
                  onClick={() => {
                    setIsOpen(false)
                    onEntryClick?.(entry)
                  }}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
