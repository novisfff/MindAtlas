import {
  startOfMonth,
  startOfWeek,
  endOfWeek,
  eachDayOfInterval,
  addDays,
  isSameDay,
  isToday as dateFnsIsToday,
  isSameMonth,
} from 'date-fns'

const CALENDAR_GRID_SIZE = 42

export function getMonthDays(date: Date): Date[] {
  const monthStart = startOfMonth(date)
  const gridStart = startOfWeek(monthStart, { weekStartsOn: 1 })

  return Array.from({ length: CALENDAR_GRID_SIZE }, (_, i) => addDays(gridStart, i))
}

export function getWeekDays(date: Date): Date[] {
  const weekStart = startOfWeek(date, { weekStartsOn: 1 })
  const weekEnd = endOfWeek(date, { weekStartsOn: 1 })

  return eachDayOfInterval({ start: weekStart, end: weekEnd })
}

export function isToday(date: Date): boolean {
  return dateFnsIsToday(date)
}

export function isSameDayAs(date1: Date, date2: Date): boolean {
  return isSameDay(date1, date2)
}

export function isCurrentMonth(date: Date, currentDate: Date): boolean {
  return isSameMonth(date, currentDate)
}
