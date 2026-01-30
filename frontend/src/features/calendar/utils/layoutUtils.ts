import {
  isSameDay,
  isWithinInterval,
  startOfDay,
  endOfDay,
  differenceInDays,
  addDays,
  isBefore,
  isAfter,
} from 'date-fns'
import type { Entry } from '@/types'

export interface LayoutEntry {
  entry: Entry
  row: number
  startCol: number
  span: number
  isStart: boolean
  isEnd: boolean
}

function getEntryDateRange(entry: Entry): { start: Date; end: Date } | null {
  if (entry.timeMode === 'POINT' && entry.timeAt) {
    const date = new Date(entry.timeAt)
    return { start: startOfDay(date), end: endOfDay(date) }
  }
  if (entry.timeMode === 'RANGE' && entry.timeFrom && entry.timeTo) {
    return {
      start: startOfDay(new Date(entry.timeFrom)),
      end: endOfDay(new Date(entry.timeTo)),
    }
  }
  return null
}

export function getEntriesForDate(entries: Entry[], date: Date): Entry[] {
  const dayStart = startOfDay(date)
  const dayEnd = endOfDay(date)

  return entries.filter((entry) => {
    const range = getEntryDateRange(entry)
    if (!range) return false

    return (
      isWithinInterval(dayStart, { start: range.start, end: range.end }) ||
      isWithinInterval(range.start, { start: dayStart, end: dayEnd })
    )
  })
}

export function isMultiDayEntry(entry: Entry): boolean {
  if (entry.timeMode !== 'RANGE' || !entry.timeFrom || !entry.timeTo) {
    return false
  }
  return !isSameDay(new Date(entry.timeFrom), new Date(entry.timeTo))
}

export function assignRows(entries: Entry[], weekStart: Date): LayoutEntry[] {
  const weekStartDay = startOfDay(weekStart)
  const weekEndDay = endOfDay(addDays(weekStart, 6))
  const result: LayoutEntry[] = []
  const rowOccupied: boolean[][] = []

  const sortedEntries = entries
    .filter((entry) => {
      const range = getEntryDateRange(entry)
      if (!range) return false
      return !isAfter(range.start, weekEndDay) && !isBefore(range.end, weekStartDay)
    })
    .sort((a, b) => {
      const rangeA = getEntryDateRange(a)!
      const rangeB = getEntryDateRange(b)!
      const startDiff = rangeA.start.getTime() - rangeB.start.getTime()
      if (startDiff !== 0) return startDiff
      const durationA = rangeA.end.getTime() - rangeA.start.getTime()
      const durationB = rangeB.end.getTime() - rangeB.start.getTime()
      return durationB - durationA
    })

  for (const entry of sortedEntries) {
    const range = getEntryDateRange(entry)!
    const clampedStart = isBefore(range.start, weekStartDay) ? weekStartDay : range.start
    const clampedEnd = isAfter(range.end, weekEndDay) ? weekEndDay : range.end

    const startCol = differenceInDays(startOfDay(clampedStart), weekStartDay)
    const endCol = differenceInDays(startOfDay(clampedEnd), weekStartDay)
    const span = endCol - startCol + 1

    let row = 0
    while (true) {
      if (!rowOccupied[row]) rowOccupied[row] = Array(7).fill(false)
      const canFit = !rowOccupied[row].slice(startCol, startCol + span).some(Boolean)
      if (canFit) break
      row++
    }

    for (let col = startCol; col < startCol + span; col++) {
      rowOccupied[row][col] = true
    }

    result.push({
      entry,
      row,
      startCol,
      span,
      isStart: !isBefore(range.start, weekStartDay),
      isEnd: !isAfter(range.end, weekEndDay),
    })
  }

  return result
}
