import type { TypeCount } from '../api/stats'
import { useTranslation } from 'react-i18next'

interface TypeDistributionProps {
  data: TypeCount[]
}

export function TypeDistribution({ data }: TypeDistributionProps) {
  const { t } = useTranslation()
  const total = data.reduce((sum, item) => sum + item.count, 0)

  if (total === 0) {
    return (
      <div className="rounded-xl border bg-card p-6 shadow-sm h-full flex flex-col items-center justify-center text-center">
        <div className="h-12 w-12 rounded-full bg-muted/20 flex items-center justify-center mb-4">
          <div className="w-6 h-6 rounded-full border-2 border-muted-foreground/30" />
        </div>
        <h3 className="font-semibold mb-2">{t('dashboard.distribution.noData')}</h3>
        <p className="text-sm text-muted-foreground">
          {t('dashboard.distribution.description')}
        </p>
      </div>
    )
  }

  // Sort by count descending
  const sortedData = [...data].sort((a, b) => b.count - a.count)

  return (
    <div className="rounded-xl border bg-card p-6 shadow-sm h-full">
      <h3 className="font-semibold mb-6">{t('dashboard.distribution.title')}</h3>
      <div className="space-y-4">
        {sortedData.map((item) => {
          const percentage = total > 0 ? (item.count / total) * 100 : 0
          return (
            <div key={item.typeId} className="group">
              <div className="flex items-center justify-between text-sm mb-2">
                <div className="flex items-center gap-2">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{
                      backgroundColor: item.typeColor || '#6B7280',
                      boxShadow: `0 0 0 2px ${(item.typeColor || '#6B7280')}33`
                    }}
                  />
                  <span className="font-medium">{item.typeName}</span>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span className="text-xs">{percentage.toFixed(0)}%</span>
                  <span className="text-xs">•</span>
                  <span>{item.count}</span>
                </div>
              </div>
              <div className="h-2.5 rounded-full bg-muted/50 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500 ease-out group-hover:opacity-90"
                  style={{
                    width: `${percentage}%`,
                    backgroundColor: item.typeColor || '#6B7280',
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
