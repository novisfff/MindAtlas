import { useState, useEffect } from 'react'
import { Save, Trash2, Plug, Eye, EyeOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
    useUpdateCredentialMutation,
    useDeleteCredentialMutation,
    useTestCredentialMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
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

    return (
        <div className="space-y-6 max-w-2xl">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-medium">{t('aiProvider.providerConfig')}</h2>
                    <p className="text-sm text-muted-foreground">{t('aiProvider.providerConfigDesc')}</p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={handleTest}
                        disabled={testStatus === 'testing' || hasChanges}
                        className={`flex items-center gap-2 px-3 py-1.5 text-sm font-medium border rounded-md transition-colors
                    ${testStatus === 'success' ? 'bg-green-50 text-green-700 border-green-200' : ''}
                    ${testStatus === 'error' ? 'bg-red-50 text-red-700 border-red-200' : ''}
                    ${testStatus === 'idle' ? 'hover:bg-muted' : ''}
                `}
                        title={hasChanges ? t('aiProvider.saveBeforeTest') : t('aiProvider.testConnection')}
                    >
                        <Plug className={`w-4 h-4 ${testStatus === 'testing' ? 'animate-pulse' : ''}`} />
                        {testStatus === 'testing' && t('aiProvider.testing')}
                        {testStatus === 'success' && t('aiProvider.connected')}
                        {testStatus === 'error' && t('aiProvider.failed')}
                        {testStatus === 'idle' && t('aiProvider.testConnection')}
                    </button>
                    <button
                        onClick={() => setDeleteId(credential.id)}
                        className="p-2 text-destructive hover:bg-destructive/10 rounded-md transition-colors"
                        title="Delete Provider"
                    >
                        <Trash2 className="w-4 h-4" />
                    </button>
                </div>
            </div>

            {testMessage && (
                <div className="p-3 text-sm bg-destructive/10 text-destructive rounded-md">
                    Error: {testMessage}
                </div>
            )}

            <div className="grid gap-4 p-4 border rounded-xl bg-card">
                <div className="grid gap-2">
                    <label className="text-sm font-medium">{t('labels.name')}</label>
                    <input
                        type="text"
                        value={formData.name}
                        onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    />
                </div>

                <div className="grid gap-2">
                    <label className="text-sm font-medium">Base URL</label>
                    <input
                        type="text"
                        value={formData.baseUrl}
                        onChange={(e) => setFormData({ ...formData, baseUrl: e.target.value })}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    />
                </div>

                <div className="grid gap-2">
                    <label className="text-sm font-medium">
                        API Key <span className="text-muted-foreground font-normal">({t('form.optional')})</span>
                    </label>
                    <div className="relative">
                        <input
                            type={showApiKey ? "text" : "password"}
                            value={formData.apiKey}
                            onChange={(e) => setFormData({ ...formData, apiKey: e.target.value })}
                            placeholder={t('settings.tools.leaveBlank')}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 pr-10"
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

                <div className="flex justify-end pt-2">
                    <button
                        onClick={handleSave}
                        disabled={!hasChanges || updateMutation.isPending}
                        className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors"
                    >
                        <Save className="w-4 h-4" />
                        {updateMutation.isPending ? t('messages.loading') : t('actions.save')}
                    </button>
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
