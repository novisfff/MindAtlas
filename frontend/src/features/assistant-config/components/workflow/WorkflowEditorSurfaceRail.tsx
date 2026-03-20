import type { ReactNode } from 'react'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/lib/utils'

export interface WorkflowEditorSurfaceRailItem<T extends string> {
  id: T
  label: string
  icon: ReactNode
  disabled?: boolean
}

interface WorkflowEditorSurfaceRailProps<T extends string> {
  items: Array<WorkflowEditorSurfaceRailItem<T>>
  activeItem: T | null
  onSelect: (id: T) => void
  className?: string
}

export function WorkflowEditorSurfaceRail<T extends string>({
  items,
  activeItem,
  onSelect,
  className,
}: WorkflowEditorSurfaceRailProps<T>) {
  return (
    <div
      className={cn(
        'hidden lg:flex pointer-events-auto flex-col gap-1 rounded-[16px] border border-white/80 bg-white/85 p-1 shadow-[0_8px_32px_rgba(0,0,0,0.06)] ring-1 ring-slate-900/5 backdrop-blur-xl z-50',
        className,
      )}
    >
      {items.map((item) => {
        const isActive = activeItem === item.id
        return (
          <Tooltip key={item.id} content={item.label}>
            <button
              type="button"
              disabled={item.disabled}
              aria-label={item.label}
              aria-pressed={isActive}
              title={item.label}
              onClick={() => onSelect(item.id)}
              className={cn(
                'group relative flex h-8 w-8 items-center justify-center rounded-[12px] border transition-all duration-200 ease-out',
                isActive
                  ? 'border-blue-100/50 bg-blue-50/80 text-blue-600 shadow-sm ring-1 ring-blue-500/10'
                  : 'border-transparent text-slate-400 hover:text-slate-700 hover:bg-slate-100/50',
                item.disabled ? 'cursor-not-allowed opacity-35 hover:border-transparent hover:bg-transparent hover:text-slate-400 hover:shadow-none' : '',
              )}
            >
              <span className={cn(
                'flex h-3.5 w-3.5 items-center justify-center transition-transform duration-150',
                isActive ? 'scale-100' : 'group-hover:scale-[1.04]',
              )}
              >
                {item.icon}
              </span>
            </button>
          </Tooltip>
        )
      })}
    </div>
  )
}
