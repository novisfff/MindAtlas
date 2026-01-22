import { useState } from 'react'
import { Plus, Trash2, Search, Download, Check, Bot, BrainCircuit } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
    useModelsQuery,
    useCreateModelMutation,
    useDeleteModelMutation,
    useDiscoverModelsByCredentialMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import type { AiCredential } from '../api/credentials'
import type { AiModelType } from '../api/models'

interface ProviderModelsProps {
    credential: AiCredential
}

export function ProviderModels({ credential }: ProviderModelsProps) {
    const { t } = useTranslation()
    const { data: allModels = [] } = useModelsQuery()
    const createMutation = useCreateModelMutation()
    const deleteMutation = useDeleteModelMutation()
    const discoverMutation = useDiscoverModelsByCredentialMutation()

    // Filter models for this credential
    const models = allModels.filter(m => m.credentialId === credential.id)

    const [isAdding, setIsAdding] = useState(false)
    const [deleteId, setDeleteId] = useState<string | null>(null)

    // Add Model Dialog State
    const [newModelName, setNewModelName] = useState('')
    const [newModelType, setNewModelType] = useState<AiModelType>('llm')
    const [fetchedModels, setFetchedModels] = useState<Array<{ name: string; suggestedType: AiModelType }>>([])
    const [isFetching, setIsFetching] = useState(false)
    const [fetchError, setFetchError] = useState<string | null>(null)

    const handleFetchModels = async () => {
        setIsFetching(true)
        setFetchError(null)
        try {
            const result = await discoverMutation.mutateAsync(credential.id)
            if (result.ok && result.models.length > 0) {
                setFetchedModels(result.models)
            } else {
                setFetchError('No models found or API returned empty list.')
                setFetchedModels([])
            }
        } catch (err) {
            setFetchError('Failed to fetch models: ' + (err as Error).message)
        } finally {
            setIsFetching(false)
        }
    }

    const handleAddModel = async () => {
        if (!newModelName) return

        await createMutation.mutateAsync({
            credentialId: credential.id,
            name: newModelName,
            modelType: newModelType
        })

        handleCloseAdd()
    }

    const handleCloseAdd = () => {
        setIsAdding(false)
        setNewModelName('')
        setNewModelType('llm')
        setFetchedModels([])
        setFetchError(null)
    }

    const handleDelete = async () => {
        if (deleteId) {
            await deleteMutation.mutateAsync(deleteId)
            setDeleteId(null)
        }
    }

    return (
        <div className="space-y-4 max-w-2xl">
            <div className="flex items-center justify-between">
                <div>
                    <h3 className="text-lg font-medium">{t('settings.ai.sections.models')}</h3>
                    <p className="text-sm text-muted-foreground">{t('aiProvider.availableModels')}</p>
                </div>
                <button
                    onClick={() => setIsAdding(true)}
                    className="inline-flex items-center gap-2 px-3 py-1.5 bg-secondary text-secondary-foreground rounded-md hover:bg-secondary/80 text-sm font-medium transition-colors"
                >
                    <Plus className="w-4 h-4" />
                    {t('settings.ai.addModel')}
                </button>
            </div>

            <div className="border rounded-xl bg-card">
                {models.length === 0 ? (
                    <div className="p-8 text-center text-muted-foreground text-sm">
                        {t('aiProvider.noModels')}
                    </div>
                ) : (
                    <div className="divide-y">
                        {models.map((model) => (
                            <div key={model.id} className="flex items-center justify-between p-3 hover:bg-muted/50 transition-colors">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-primary/10 rounded-md text-primary">
                                        {model.modelType === 'embedding' ? (
                                            <BrainCircuit className="w-4 h-4" />
                                        ) : (
                                            <Bot className="w-4 h-4" />
                                        )}
                                    </div>
                                    <div>
                                        <div className="font-medium">{model.name}</div>
                                        <div className="text-xs text-muted-foreground capitalize">{model.modelType}</div>
                                    </div>
                                </div>
                                <button
                                    onClick={() => setDeleteId(model.id)}
                                    className="p-2 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-md transition-colors"
                                >
                                    <Trash2 className="w-4 h-4" />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Add Model Dialog */}
            <Dialog open={isAdding} onOpenChange={(open) => !open && handleCloseAdd()}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{t('settings.ai.addModel')}</DialogTitle>
                    </DialogHeader>

                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">{t('settings.ai.modelName')}</label>
                            <div className="relative">
                                <input
                                    type="text"
                                    value={newModelName}
                                    onChange={(e) => setNewModelName(e.target.value)}
                                    placeholder={t('settings.ai.modelName') + "..."}
                                    className="w-full px-3 py-2 border rounded-md pr-24"
                                />
                                <button
                                    onClick={handleFetchModels}
                                    disabled={isFetching}
                                    className="absolute right-1 top-1 bottom-1 px-3 text-xs bg-muted hover:bg-muted/80 rounded flex items-center gap-1 transition-colors"
                                >
                                    {isFetching ? <Search className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                                    {isFetching ? t('aiProvider.fetching') : t('aiProvider.fetchList')}
                                </button>
                            </div>
                            {fetchError && (
                                <p className="text-xs text-destructive">{fetchError}</p>
                            )}
                        </div>

                        {fetchedModels.length > 0 && (
                            <div className="space-y-2 border rounded-md p-2 max-h-40 overflow-y-auto bg-muted/30">
                                <div className="text-xs text-muted-foreground px-1 pb-1">{t('aiProvider.selectFromFetched')}</div>
                                <div className="grid gap-1">
                                    {fetchedModels.map((m) => (
                                        <button
                                            key={m.name}
                                            onClick={() => {
                                                setNewModelName(m.name)
                                                // Defaulting to LLM as per requirement, ignoring suggested type for now
                                                // setNewModelType(m.suggestedType) 
                                            }}
                                            className={`text-left text-sm px-2 py-1.5 rounded flex items-center justify-between hover:bg-primary/10 transition-colors
                                        ${newModelName === m.name ? 'bg-primary/10 text-primary font-medium' : ''}
                                    `}
                                        >
                                            <span>{m.name}</span>
                                            {newModelName === m.name && <Check className="w-3 h-3" />}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    <DialogFooter>
                        <button
                            onClick={handleCloseAdd}
                            className="px-4 py-2 text-sm font-medium hover:bg-muted rounded-md transition-colors"
                        >
                            {t('actions.cancel')}
                        </button>
                        <button
                            onClick={handleAddModel}
                            disabled={!newModelName || createMutation.isPending}
                            className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                        >
                            {createMutation.isPending ? t('messages.loading') : t('actions.add')}
                        </button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

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
