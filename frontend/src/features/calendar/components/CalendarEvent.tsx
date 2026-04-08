import { useRef, type CSSProperties } from 'react'
import { useDraggable } from '@dnd-kit/core'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'
import type { CalendarDensity } from '../types'
import { calendarRadius } from '../styles'

interface CalendarEventProps {
  entry: Entry
  density?: CalendarDensity
  compact?: boolean
  isDragging?: boolean
  resizable?: boolean
  showStartIndicator?: boolean
  onClick?: () => void
  onResizeStart?: (
    entry: Entry,
    direction: 'left' | 'right',
    e: React.PointerEvent,
  ) => void
}

function withAlpha(color: string, opacity: number): string {
  const normalized = color.trim()
  const alphaHex = Math.round(Math.min(Math.max(opacity, 0), 1) * 255)
    .toString(16)
    .padStart(2, '0')

  if (/^#([0-9a-f]{3})$/i.test(normalized)) {
    const expanded = normalized
      .slice(1)
      .split('')
      .map((char) => char + char)
      .join('')
    return `#${expanded}${alphaHex}`
  }

  if (/^#([0-9a-f]{6}|[0-9a-f]{8})$/i.test(normalized)) {
    return `#${normalized.slice(1, 7)}${alphaHex}`
  }

  return `color-mix(in srgb, ${normalized} ${Math.round(opacity * 100)}%, transparent)`
}

export function CalendarEvent({
  entry,
  density = 'comfortable',
  compact = false,
  isDragging = false,
  resizable = false,
  showStartIndicator = true,
  onClick,
  onResizeStart,
}: CalendarEventProps) {
  const suppressClickRef = useRef(false)
  const draggableId = isDragging ? `overlay-${entry.id}` : entry.id
  const isDense = density === 'compact'
  const isInlineCompact = compact || isDense

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    isDragging: isDraggingInternal,
  } = useDraggable({
    id: draggableId,
    disabled: isDragging,
  })
  const accentColor = entry.type?.color || 'hsl(var(--foreground) / 0.8)'
  const compactBgColor = entry.type?.color
    ? withAlpha(entry.type.color, showStartIndicator ? 0.14 : 0.09)
    : 'hsl(var(--muted) / 0.6)'
  const bgColor = entry.type?.color
    ? showStartIndicator
      ? `linear-gradient(90deg, ${withAlpha(entry.type.color, isInlineCompact ? 0.13 : 0.16)} 0%, ${withAlpha(entry.type.color, isInlineCompact ? 0.05 : 0.08)} 30%, hsl(var(--background) / 0.95) 100%)`
      : `linear-gradient(90deg, ${withAlpha(entry.type.color, isInlineCompact ? 0.06 : 0.09)} 0%, hsl(var(--background) / 0.95) 72%)`
    : 'hsl(var(--background) / 0.9)'
  const effectiveBackground = isInlineCompact ? compactBgColor : bgColor
  const ringColor = entry.type?.color
    ? withAlpha(entry.type.color, isInlineCompact ? 0.18 : 0.22)
    : 'hsl(var(--border) / 0.95)'
  const compactRingColor = entry.type?.color
    ? withAlpha(entry.type.color, 0.08)
    : 'hsl(var(--border) / 0.55)'
  const shadowColor = entry.type?.color
    ? withAlpha(entry.type.color, isInlineCompact ? 0.08 : 0.12)
    : 'hsl(var(--foreground) / 0.06)'
  const leftRadiusClass = showStartIndicator
    ? calendarRadius.eventStart
    : calendarRadius.eventContinue

  const hideOriginalWhileDragging = isDraggingInternal && !isDragging

  const style: CSSProperties = {
    background: effectiveBackground,
    borderLeft: `${isInlineCompact ? 4 : 3}px solid ${showStartIndicator ? accentColor : withAlpha(accentColor, 0.14)}`,
    boxShadow: isInlineCompact
      ? `inset 0 0 0 1px ${compactRingColor}`
      : `inset 0 0 0 1px ${ringColor}, 0 1px 2px ${shadowColor}`,
    transform: transform
      ? `translate(${transform.x}px, ${transform.y}px)`
      : undefined,
    opacity: hideOriginalWhileDragging ? 0 : isDragging ? 0.8 : 1,
    visibility: hideOriginalWhileDragging ? 'hidden' : undefined,
    pointerEvents: hideOriginalWhileDragging ? 'none' : undefined,
  }

  const handleResize =
    (direction: 'left' | 'right') => (e: React.PointerEvent) => {
      e.preventDefault()
      e.stopPropagation()
      e.nativeEvent.stopImmediatePropagation?.()
      e.currentTarget.setPointerCapture?.(e.pointerId)
      suppressClickRef.current = true
      onResizeStart?.(entry, direction, e)
    }

  const handleResizePointerUp = (e: React.PointerEvent) => {
    window.setTimeout(() => {
      suppressClickRef.current = false
    }, 0)
  }

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      className={cn(
        'group relative block cursor-grab overflow-hidden text-foreground/90 transition-[box-shadow,transform,background] active:cursor-grabbing',
        isInlineCompact
          ? `h-[18px] px-2 text-[10px] font-semibold leading-[18px] text-foreground/95 ${calendarRadius.eventEnd}`
          : `min-h-[34px] px-3 py-1.5 text-xs font-medium leading-5 ${calendarRadius.eventEnd}`,
        leftRadiusClass,
        isDragging && 'shadow-lg ring-1 ring-black/10',
      )}
      style={style}
      title={entry.title}
      onClick={(e) => {
        e.stopPropagation()
        if (hideOriginalWhileDragging) return
        if (suppressClickRef.current) {
          suppressClickRef.current = false
          e.preventDefault()
          return
        }
        onClick?.()
      }}
    >
      <div
        className={cn(
          'pointer-events-none absolute inset-0 opacity-0 transition-opacity group-hover:opacity-100',
          isInlineCompact
            ? 'bg-black/[0.02] dark:bg-white/[0.03]'
            : 'bg-black/[0.025] dark:bg-white/[0.04]',
        )}
      />
      <div className="relative z-10 block truncate pr-1">{entry.title}</div>

      {/* Resize Handles - Only visible on hover and if resizable */}
      {resizable && !isDragging && (
        <>
          {/* Left Handle */}
          <div
            className="absolute bottom-0 left-0 top-0 z-10 w-2 cursor-ew-resize select-none touch-none opacity-0 transition-opacity group-hover:opacity-100 hover:bg-black/[0.08] dark:hover:bg-white/[0.1]"
            onPointerDown={handleResize('left')}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
          />
          {/* Right Handle */}
          <div
            className="absolute bottom-0 right-0 top-0 z-10 w-2 cursor-ew-resize select-none touch-none opacity-0 transition-opacity group-hover:opacity-100 hover:bg-black/[0.08] dark:hover:bg-white/[0.1]"
            onPointerDown={handleResize('right')}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
          />
        </>
      )}
    </div>
  )
}
