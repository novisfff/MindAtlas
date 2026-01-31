import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { FolderOpen, Hash, Loader2, TrendingUp } from 'lucide-react'
import { useHotnessQuery } from '../queries'
import { cn } from '@/lib/utils'

export function TypeTagHotness() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading } = useHotnessQuery()

  if (isLoading) {
    return (
      <div className="rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-center justify-center h-[400px]">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  const maxTypeCount = Math.max(...(data?.topTypes.map((t) => t.count) || [1]))
  const maxTagCount = Math.max(...(data?.topTags.map((t) => t.count) || [1]))

  return (
    <div className="rounded-xl border bg-card shadow-sm h-full flex flex-col">
      <div className="p-4 border-b bg-muted/10">
        <div className="flex items-center gap-2 text-foreground/80">
          <TrendingUp className="w-4 h-4" />
          <h3 className="font-semibold text-sm">{t('dashboard.hotness.title', 'Popularity Trends')}</h3>
        </div>
      </div>

      <div className="p-4 space-y-5 flex-1 overflow-auto">
        {/* Top Types */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <FolderOpen className="w-3.5 h-3.5" />
              {t('dashboard.hotness.topTypes')}
            </h4>
          </div>

          <div className="space-y-1">
            {data?.topTypes.length === 0 && (
              <p className="text-xs text-muted-foreground py-2 text-center bg-muted/30 rounded-lg">
                {t('dashboard.hotness.noData')}
              </p>
            )}
            {data?.topTypes.slice(0, 5).map((type, index) => (
              <HotnessItem
                key={type.typeId}
                rank={index + 1}
                label={type.typeName}
                count={type.count}
                maxCount={maxTypeCount}
                color={type.typeColor}
                onClick={() => navigate(`/entries?type=${type.typeId}`)}
              />
            ))}
          </div>
        </div>

        {/* Top Tags */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Hash className="w-3.5 h-3.5" />
              {t('dashboard.hotness.topTags')}
            </h4>
          </div>

          <div className="space-y-1">
            {data?.topTags.length === 0 && (
              <p className="text-xs text-muted-foreground py-2 text-center bg-muted/30 rounded-lg">
                {t('dashboard.hotness.noData')}
              </p>
            )}
            {data?.topTags.slice(0, 5).map((tag, index) => (
              <HotnessItem
                key={tag.tagId}
                rank={index + 1}
                label={tag.tagName}
                count={tag.count}
                maxCount={maxTagCount}
                color={tag.tagColor}
                onClick={() => navigate(`/entries?tags=${tag.tagId}`)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function HotnessItem({
  rank,
  label,
  count,
  maxCount,
  color,
  onClick,
}: {
  rank: number
  label: string
  count: number
  maxCount: number
  color: string | null
  onClick: () => void
}) {
  const percentage = (count / maxCount) * 100

  const getRankStyle = (r: number) => {
    switch (r) {
      case 1: return "bg-yellow-100/80 text-yellow-700 dark:bg-yellow-500/10 dark:text-yellow-500"
      case 2: return "bg-zinc-100 text-zinc-700 dark:bg-zinc-500/10 dark:text-zinc-400"
      case 3: return "bg-orange-100/80 text-orange-700 dark:bg-orange-500/10 dark:text-orange-500"
      default: return "bg-muted/50 text-muted-foreground"
    }
  }

  return (
    <button
      onClick={onClick}
      className="group w-full flex items-center gap-2.5 px-2 py-1 -mx-2 rounded-lg hover:bg-muted/50 transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      {/* Rank Badge */}
      <div className={cn(
        "flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[9px] font-bold shadow-sm ring-1 ring-inset ring-black/5 dark:ring-white/5",
        getRankStyle(rank)
      )}>
        {rank}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-0.5">
          <div className="flex items-center gap-2 min-w-0">
            {/* Color Dot if needed for visual pop */}
            {color && (
              <div
                className="w-1.5 h-1.5 rounded-full shrink-0 shadow-[0_0_4px_currentColor]"
                style={{ color: color, backgroundColor: color }}
              />
            )}
            <span className="text-sm font-medium truncate text-foreground/90 group-hover:text-primary transition-colors">
              {label}
            </span>
          </div>
          <span className="text-[10px] font-medium text-muted-foreground bg-muted/50 px-1.5 rounded-md min-w-[1.25rem] text-center">
            {count}
          </span>
        </div>

        {/* Progress Bar */}
        <div className="h-1 w-full bg-muted/40 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500 ease-out group-hover:brightness-110"
            style={{
              width: `${percentage}%`,
              backgroundColor: color || 'hsl(var(--primary))',
              opacity: 0.8
            }}
          />
        </div>
      </div>
    </button>
  )
}
