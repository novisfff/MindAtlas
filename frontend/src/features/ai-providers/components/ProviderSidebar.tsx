import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { uiChrome } from '@/components/ui/styles'
import { SettingsBadge, SettingsEmptyState, SettingsInset } from '@/features/settings/components/SettingsShell'
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
        <div className={cn('flex h-full min-h-0 flex-col p-4', className)}>
            <div className="space-y-4">
                <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-foreground">
                        {t('settings.ai.sections.credentials')}
                    </h3>
                    <SettingsBadge>{credentials.length}</SettingsBadge>
                </div>

                <Button onClick={onAdd} className="w-full justify-center">
                    <Plus className="h-4 w-4" />
                    {t('settings.ai.providers.add')}
                </Button>
            </div>

            <div className="mt-4 flex-1 overflow-y-auto">
                {credentials.length === 0 ? (
                    <SettingsEmptyState
                        title={t('aiProvider.noProviders')}
                        description={t('pages.aiProviders.description')}
                        className="px-4 py-10"
                    />
                ) : (
                    <div className="space-y-2 pr-1">
                        {credentials.map((cred) => (
                            <button
                                key={cred.id}
                                onClick={() => onSelect(cred.id)}
                                className={cn(
                                    uiChrome.control,
                                    'w-full space-y-1 px-3 py-3 text-left shadow-none transition-colors',
                                    selectedId === cred.id
                                        ? 'border-primary/20 bg-primary/8 text-foreground'
                                        : 'hover:bg-muted/55 text-muted-foreground hover:text-foreground'
                                )}
                            >
                                <span className="block truncate font-medium text-foreground">{cred.name}</span>
                                <span className="block truncate text-xs text-muted-foreground">{cred.baseUrl}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>

            <SettingsInset className="mt-4">
                <p className="text-xs leading-6 text-muted-foreground">
                    {t('aiProvider.providerConfigDesc')}
                </p>
            </SettingsInset>
        </div>
    )
}
