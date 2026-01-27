import { Plus, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { AiCredential } from '../api/credentials'

interface ProviderSidebarProps {
    credentials: AiCredential[]
    selectedId: string | null
    onSelect: (id: string) => void
    onAdd: () => void
    className?: string
}

export function ProviderSidebar({
    credentials,
    selectedId,
    onSelect,
    onAdd,
    className,
}: ProviderSidebarProps) {
    const { t } = useTranslation()

    return (
        <div className={cn('flex flex-col h-full border-r bg-muted/10', className)}>
            <div className="p-4 space-y-4">
                {/* Header / Search could go here if needed */}
                <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wider">
                        {t('settings.ai.sections.credentials')}
                    </h3>
                </div>

                <button
                    onClick={onAdd}
                    className="w-full flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 text-sm font-medium transition-colors"
                >
                    <Plus className="w-4 h-4" />
                    {t('settings.ai.providers.add')}
                </button>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-1">
                {credentials.map((cred) => (
                    <button
                        key={cred.id}
                        onClick={() => onSelect(cred.id)}
                        className={cn(
                            'w-full flex flex-col items-start px-3 py-2 rounded-lg text-left transition-colors',
                            selectedId === cred.id
                                ? 'bg-primary/10 text-primary'
                                : 'hover:bg-muted text-muted-foreground hover:text-foreground'
                        )}
                    >
                        <span className="font-medium truncate w-full">{cred.name}</span>
                        <span className="text-xs opacity-70 truncate w-full">{cred.baseUrl}</span>
                    </button>
                ))}

                {credentials.length === 0 && (
                    <div className="text-center py-8 text-sm text-muted-foreground">
                        {t('aiProvider.noProviders')}
                    </div>
                )}
            </div>
        </div>
    )
}
