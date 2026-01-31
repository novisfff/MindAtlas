import React, { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ActivityCalendar, type Activity } from 'react-activity-calendar'
import { useHeatmapQuery } from '../queries'
import { useEntryTypesQuery } from '@/features/entry-types/queries'
import { cn } from '@/lib/utils'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { ChevronDown, Loader2 } from 'lucide-react'

export function ActivityHeatmap() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [selectedTypeId, setSelectedTypeId] = useState<string | undefined>()
  const [typeFilterOpen, setTypeFilterOpen] = useState(false)

  const { data: entryTypes } = useEntryTypesQuery()
  const { data: heatmapData, isLoading } = useHeatmapQuery({
    months: 3,
    typeId: selectedTypeId,
  })

  const activities: Activity[] = useMemo(() => {
    if (!heatmapData?.data) return []
    return heatmapData.data.map((day) => ({
      date: day.date,
      count: day.count,
      level: Math.min(4, day.count) as 0 | 1 | 2 | 3 | 4,
    }))
  }, [heatmapData])

  const dayDataMap = useMemo(() => {
    const map = new Map<string, { rangeStartCount: number; rangeActiveCount: number }>()
    heatmapData?.data.forEach((d) => {
      map.set(d.date, {
        rangeStartCount: d.rangeStartCount ?? 0,
        rangeActiveCount: d.rangeActiveCount ?? 0,
      })
    })
    return map
  }, [heatmapData])

  const selectedTypeName = useMemo(() => {
    if (!selectedTypeId) return t('dashboard.heatmap.allTypes')
    return entryTypes?.find((t) => t.id === selectedTypeId)?.name ?? ''
  }, [selectedTypeId, entryTypes, t])

  const handleDayClick = (activity: Activity) => {
    navigate(`/calendar?date=${activity.date}`)
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-center justify-center h-32">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border bg-card p-6 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold">{t('dashboard.heatmap.title')}</h3>
        <Popover open={typeFilterOpen} onOpenChange={setTypeFilterOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1">
              {selectedTypeName}
              <ChevronDown className="h-4 w-4 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-48 p-1" align="end">
            <button
              className={cn(
                'w-full text-left px-3 py-2 text-sm rounded-md hover:bg-muted',
                !selectedTypeId && 'bg-muted'
              )}
              onClick={() => {
                setSelectedTypeId(undefined)
                setTypeFilterOpen(false)
              }}
            >
              {t('dashboard.heatmap.allTypes')}
            </button>
            {entryTypes?.map((type) => (
              <button
                key={type.id}
                className={cn(
                  'w-full text-left px-3 py-2 text-sm rounded-md hover:bg-muted flex items-center gap-2',
                  selectedTypeId === type.id && 'bg-muted'
                )}
                onClick={() => {
                  setSelectedTypeId(type.id)
                  setTypeFilterOpen(false)
                }}
              >
                {type.color && (
                  <span
                    className="w-3 h-3 rounded-full"
                    style={{ backgroundColor: type.color }}
                  />
                )}
                {type.name}
              </button>
            ))}
          </PopoverContent>
        </Popover>
      </div>

      <div className="overflow-x-auto">
        <ActivityCalendar
          data={activities}
          blockSize={12}
          blockMargin={3}
          fontSize={12}
          showColorLegend
          showMonthLabels
          showTotalCount={false}
          showWeekdayLabels
          theme={{
            light: ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39'],
            dark: ['#161b22', '#0e4429', '#006d32', '#26a641', '#39d353'],
          }}
          renderBlock={(block, activity) => {
            const dayInfo = dayDataMap.get(activity.date)
            // Pure span day: has active range events but no new starts
            const isPureSpan =
              (dayInfo?.rangeActiveCount ?? 0) > 0 &&
              (dayInfo?.rangeStartCount ?? 0) === 0

            let style = block.props.style
            if (isPureSpan && activity.count > 0) {
              style = {
                ...style,
                backgroundImage: `repeating-linear-gradient(
                  45deg,
                  transparent,
                  transparent 3px,
                  rgba(255,255,255,0.25) 3px,
                  rgba(255,255,255,0.25) 6px
                )`,
              }
            }

            return (
              <HeatmapTooltip activity={activity} onDayClick={handleDayClick}>
                {React.cloneElement(block, { style })}
              </HeatmapTooltip>
            )
          }}
        />
      </div>
    </div>
  )
}

function HeatmapTooltip({
  activity,
  children,
  onDayClick,
}: {
  activity: Activity
  children: React.ReactNode
  onDayClick: (activity: Activity) => void
}) {
  const { t } = useTranslation()

  if (activity.count === 0) {
    return <>{children}</>
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <g onClick={() => onDayClick(activity)} style={{ cursor: 'pointer' }}>
          {children}
        </g>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-2" side="top">
        <p className="text-sm font-medium">
          {t('dashboard.heatmap.entriesOnDate', {
            count: activity.count,
            date: activity.date,
          })}
        </p>
      </PopoverContent>
    </Popover>
  )
}
