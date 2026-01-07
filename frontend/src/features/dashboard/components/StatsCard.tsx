import { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface StatsCardProps {
  title: string
  value: number | string
  icon: LucideIcon
  color?: string
  description?: string
}

export function StatsCard({ title, value, icon: Icon, color, description }: StatsCardProps) {
  return (
    <div className="group relative overflow-hidden rounded-xl border bg-card p-6 shadow-sm transition-all duration-300 hover:shadow-md hover:-translate-y-1">
      <div className="absolute inset-0 bg-gradient-to-br from-transparent to-muted/20 opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
      <div className="relative flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <p className="text-3xl font-bold tracking-tight mt-2">{value}</p>
          {description && (
            <p className="text-xs text-muted-foreground mt-1">{description}</p>
          )}
        </div>
        <div
          className={cn(
            'flex h-12 w-12 items-center justify-center rounded-xl transition-colors duration-300',
            'bg-primary/5 group-hover:bg-primary/10'
          )}
          style={color ? { backgroundColor: `${color}15` } : undefined}
        >
          <Icon
            className="h-6 w-6 transition-transform duration-300 group-hover:scale-110"
            style={color ? { color } : undefined}
          />
        </div>
      </div>
    </div>
  )
}
