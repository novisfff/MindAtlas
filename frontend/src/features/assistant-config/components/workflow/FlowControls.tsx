import { memo } from 'react'
import { useReactFlow } from '@xyflow/react'
import { useTranslation } from 'react-i18next'
import {
    Plus,
    Minus,
    Maximize,
    Lock,
    Unlock,
    LucideIcon
} from 'lucide-react'

interface FlowControlsProps {
    isInteractive: boolean
    onLockChange: (locked: boolean) => void
}

export const FlowControls = memo(function FlowControls({
    isInteractive,
    onLockChange,
}: FlowControlsProps) {
    const { zoomIn, zoomOut, fitView } = useReactFlow()
    const { t } = useTranslation()

    return (
        <div className="absolute bottom-4 left-4 z-10 flex flex-row gap-1 bg-white/80 backdrop-blur-md border shadow-sm rounded-lg p-1">
            <ControlButton
                onClick={() => zoomIn()}
                icon={Plus}
                label={t('common.zoomIn')}
            />
            <ControlButton
                onClick={() => zoomOut()}
                icon={Minus}
                label={t('common.zoomOut')}
            />
            <ControlButton
                onClick={() => fitView()}
                icon={Maximize}
                label={t('common.fitView')}
            />
            <div className="w-px bg-border/50 mx-0.5 h-auto" />
            <ControlButton
                onClick={() => onLockChange(!isInteractive)}
                icon={isInteractive ? Unlock : Lock}
                label={isInteractive ? t('common.unlock') : t('common.lock')}
                active={!isInteractive}
            />
        </div>
    )
})

function ControlButton({
    onClick,
    icon: Icon,
    label,
    active,
}: {
    onClick: () => void
    icon: LucideIcon
    label: string
    active?: boolean
}) {
    return (
        <button
            onClick={onClick}
            className={`p-1.5 rounded-md transition-all duration-200 ${active
                ? 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90'
                : 'hover:bg-slate-100/80 text-muted-foreground hover:text-foreground'
                }`}
            title={label}
        >
            <Icon className="w-4 h-4" />
        </button>
    )
}
