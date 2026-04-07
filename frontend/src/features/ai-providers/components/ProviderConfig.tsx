import { useState, useEffect } from 'react'
import { Save, Trash2, Plug, Eye, EyeOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
    useUpdateCredentialMutation,
    useDeleteCredentialMutation,
    useTestCredentialMutation,
} from '../queries'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { uiField } from '@/components/ui/styles'
import {
    SettingsBadge,
    SettingsInset,
    SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'
import type { AiCredential } from '../api/credentials'

interface ProviderConfigProps {
    credential: AiCredential
    onDelete?: () => void
}

export function ProviderConfig({ credential, onDelete }: ProviderConfigProps) {
    const { t } = useTranslation()
    const updateMutation = useUpdateCredentialMutation()
    const deleteMutation = useDeleteCredentialMutation()
    const testMutation = useTestCredentialMutation()

    const [formData, setFormData] = useState({
        name: credential.name,
        baseUrl: credential.baseUrl,
        apiKey: '',
    })

    const [showApiKey, setShowApiKey] = useState(false)
    const [deleteId, setDeleteId] = useState<string | null>(null)
    const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle')
    const [testMessage, setTestMessage] = useState<string | null>(null)

    // Reset form when credential changes
    useEffect(() => {
        setFormData({
            name: credential.name,
            baseUrl: credential.baseUrl,
            apiKey: '',
        })
        setTestStatus('idle')
        setTestMessage(null)
    }, [credential.id, credential.name, credential.baseUrl])

    const handleSave = async () => {
        if (!formData.name || !formData.baseUrl) return

        await updateMutation.mutateAsync({
            id: credential.id,
            payload: {
                name: formData.name,
                baseUrl: formData.baseUrl,
                ...(formData.apiKey ? { apiKey: formData.apiKey } : {}),
            },
        })

        // Clear password field after save
        setFormData(prev => ({ ...prev, apiKey: '' }))
    }

    const handleDelete = async () => {
        if (deleteId) {
            await deleteMutation.mutateAsync(deleteId)
            setDeleteId(null)
            if (onDelete) onDelete()
        }
    }

    const handleTest = async () => {
        setTestStatus('testing')
        setTestMessage(null)
        try {
            const result = await testMutation.mutateAsync(credential.id)
            if (result.ok) {
                setTestStatus('success')
            } else {
                setTestStatus('error')
                setTestMessage(result.message || 'Connection failed')
            }
        } catch (err) {
            setTestStatus('error')
            setTestMessage('Network error or server unavailable')
        }
    }

    const hasChanges =
        formData.name !== credential.name ||
        formData.baseUrl !== credential.baseUrl ||
        formData.apiKey !== ''

    const testBadgeClassName =
        testStatus === 'success'
            ? 'border-emerald-200/80 bg-emerald-50/80 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200'
            : testStatus === 'error'
                ? 'border-destructive/25 bg-destructive/8 text-destructive'
                : 'border-border/80 bg-background/88 text-muted-foreground'

    return (
        <div className="space-y-6">
            <SettingsSectionHeader
                title={t('aiProvider.providerConfig')}
                description={t('aiProvider.providerConfigDesc')}
                actions={
                    <div className="flex flex-wrap items-center gap-2">
                        <SettingsBadge>{credential.name}</SettingsBadge>
                        <Button
                            type="button"
                            onClick={handleTest}
                            disabled={testStatus === 'testing' || hasChanges}
                            variant="outline"
                            size="sm"
                            title={hasChanges ? t('aiProvider.saveBeforeTest') : t('aiProvider.testConnection')}
                        >
                            <Plug className={cn('h-4 w-4', testStatus === 'testing' && 'animate-pulse')} />
                            {testStatus === 'testing' && t('aiProvider.testing')}
                            {testStatus === 'success' && t('aiProvider.connected')}
                            {testStatus === 'error' && t('aiProvider.failed')}
                            {testStatus === 'idle' && t('aiProvider.testConnection')}
                        </Button>
                        <Button
                            type="button"
                            onClick={() => setDeleteId(credential.id)}
                            variant="ghost"
                            size="icon"
                            className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                            title="Delete Provider"
                        >
                            <Trash2 className="h-4 w-4" />
                        </Button>
                    </div>
                }
            />

            {testStatus !== 'idle' ? (
                <SettingsBadge className={cn('w-fit', testBadgeClassName)}>
                    {testStatus === 'testing' ? t('aiProvider.testing') : testStatus === 'success' ? t('aiProvider.connected') : t('aiProvider.failed')}
                </SettingsBadge>
            ) : null}

            {testMessage && (
                <SettingsInset className="border-destructive/20 bg-destructive/5 text-destructive">
                    Error: {testMessage}
                </SettingsInset>
            )}

            <div className="grid gap-4">
                <div className="grid gap-2">
                    <label className="text-sm font-medium">{t('labels.name')}</label>
                    <input
                        type="text"
                        value={formData.name}
                        onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                        className={uiField.input}
                    />
                </div>

                <div className="grid gap-2">
                    <label className="text-sm font-medium">Base URL</label>
                    <input
                        type="text"
                        value={formData.baseUrl}
                        onChange={(e) => setFormData({ ...formData, baseUrl: e.target.value })}
                        className={uiField.input}
                    />
                </div>

                <div className="grid gap-2">
                    <label className="text-sm font-medium">
                        API Key <span className="font-normal text-muted-foreground">({t('form.optional')})</span>
                    </label>
                    <div className="relative">
                        <input
                            type={showApiKey ? "text" : "password"}
                            value={formData.apiKey}
                            onChange={(e) => setFormData({ ...formData, apiKey: e.target.value })}
                            placeholder={t('settings.tools.leaveBlank')}
                            className={cn(uiField.input, 'pr-10')}
                        />
                        <button
                            type="button"
                            onClick={() => setShowApiKey(!showApiKey)}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        >
                            {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </button>
                    </div>
                </div>

                <SettingsInset>
                    <p className="text-sm leading-6 text-muted-foreground">
                        {t('aiProvider.saveBeforeTest')}
                    </p>
                </SettingsInset>

                <div className="flex justify-end pt-2">
                    <Button
                        type="button"
                        onClick={handleTest}
                        disabled={!hasChanges || updateMutation.isPending}
                        variant="outline"
                        className="mr-3"
                    >
                        <Plug className="h-4 w-4" />
                        {t('aiProvider.testConnection')}
                    </Button>
                    <Button
                        type="button"
                        onClick={handleSave}
                        disabled={!hasChanges || updateMutation.isPending}
                    >
                        <Save className="h-4 w-4" />
                        {updateMutation.isPending ? t('messages.loading') : t('actions.save')}
                    </Button>
                </div>
            </div>

            <ConfirmDialog
                isOpen={!!deleteId}
                onCancel={() => setDeleteId(null)}
                onConfirm={handleDelete}
                title={t('actions.delete')}
                description={t('messages.confirmDelete')}
                variant="destructive"
            />
        </div>
    )
}
