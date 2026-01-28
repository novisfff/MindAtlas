import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

export type GraphMode = 'system' | 'lightrag'

// Custom SVG Icons
export function SystemGraphIcon({ className }: { className?: string }) {
    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
            <circle cx="12" cy="5" r="3" />
            <circle cx="5" cy="19" r="3" />
            <circle cx="19" cy="19" r="3" />
            <path d="M10.5 7.5L6.5 16.5" />
            <path d="M13.5 7.5L17.5 16.5" />
            <path d="M8 19h11" />
        </svg>
    )
}

export function AiGraphIcon({ className }: { className?: string }) {
    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
            <circle cx="12" cy="12" r="3" />
            <circle cx="19" cy="5" r="2" />
            <circle cx="5" cy="5" r="2" />
            <circle cx="19" cy="19" r="2" />
            <circle cx="5" cy="19" r="2" />
            <path d="M10.5 10.5L6.5 6.5" />
            <path d="M13.5 13.5L17.5 17.5" />
            <path d="M13.5 10.5L17.5 6.5" />
            <path d="M10.5 13.5L6.5 17.5" />
            {/* Sparkle element */}
            <path d="M12 2v2" className="opacity-50" />
            <path d="M12 20v2" className="opacity-50" />
            <path d="M2 12h2" className="opacity-50" />
            <path d="M20 12h2" className="opacity-50" />
        </svg>
    )
}

interface ModeSwitchProps {
    mode: GraphMode
    onModeChange: (mode: GraphMode) => void
}

export function ModeSwitch({ mode, onModeChange }: ModeSwitchProps) {
    const { t } = useTranslation()

    return (
        <div className="flex items-center p-1 bg-muted rounded-lg border">
            <button
                onClick={() => onModeChange('system')}
                className={cn(
                    "flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-md transition-all",
                    mode === 'system'
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                )}
            >
                <SystemGraphIcon className="w-4 h-4" />
                {t('pages.graph.modes.system')}
            </button>
            <button
                onClick={() => onModeChange('lightrag')}
                className={cn(
                    "flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-md transition-all",
                    mode === 'lightrag'
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                )}
            >
                <AiGraphIcon className="w-4 h-4" />
                {t('pages.graph.modes.lightrag')}
            </button>
        </div>
    )
}
