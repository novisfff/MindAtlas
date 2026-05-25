import { useState } from 'react'
import { Plus, Trash2, Search, Download, Check, Bot, BrainCircuit } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
    useModelsQuery,
    useCreateModelMutation,
    useDeleteModelMutation,
    useDiscoverModelsByCredentialMutation,
    useModelBindingsQuery,
} from '../queries'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { uiChrome, uiField } from '@/components/ui/styles'
import {
    SettingsBadge,
    SettingsEmptyState,
    SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'
import type { AiCredential } from '../api/credentials'
import type { AiModelType } from '../api/models'

interface ProviderModelsProps {
    credential: AiCredential
}

export function ProviderModels({ credential }: ProviderModelsProps) {
    const { t } = useTranslation()
    const { data: allModels = [] } = useModelsQuery()
    const { data: bindings } = useModelBindingsQuery()
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

    const getBoundUsages = (modelId: string | null) => {
        if (!modelId || !bindings) return []
        const usages: string[] = []
        if (bindings.assistant?.llmModelId === modelId) {
            usages.push(`${t('settings.ai.roles.system')} / ${t('settings.ai.modelTypes.llm')}`)
        }
        if (bindings.assistant?.embeddingModelId === modelId) {
            usages.push(`${t('settings.ai.roles.system')} / ${t('settings.ai.modelTypes.embedding')}`)
        }
        if (bindings.lightrag?.llmModelId === modelId) {
            usages.push(`${t('settings.ai.roles.lightrag')} / ${t('settings.ai.modelTypes.llm')}`)
        }
        if (bindings.lightrag?.embeddingModelId === modelId) {
            usages.push(`${t('settings.ai.roles.lightrag')} / ${t('settings.ai.modelTypes.embedding')}`)
        }
        if (bindings.workflowCopilot?.llmModelId === modelId) {
            usages.push(`${t('settings.ai.roles.workflowCopilot')} / ${t('settings.ai.modelTypes.llm')}`)
        }
        if (bindings.workflowCopilot?.embeddingModelId === modelId) {
            usages.push(`${t('settings.ai.roles.workflowCopilot')} / ${t('settings.ai.modelTypes.embedding')}`)
        }
        return usages
    }

    const deletingModel = models.find((model) => model.id === deleteId)
    const boundUsages = getBoundUsages(deleteId)

    const handleDelete = async () => {
        if (deleteId) {
            await deleteMutation.mutateAsync({
                id: deleteId,
                confirmBoundBindings: boundUsages.length > 0,
            })
            setDeleteId(null)
        }
    }

    return (
        <div className="space-y-4">
            <SettingsSectionHeader
                title={t('settings.ai.sections.models')}
                description={t('aiProvider.availableModels')}
                actions={
                    <Button onClick={() => setIsAdding(true)}>
                        <Plus className="h-4 w-4" />
                        {t('settings.ai.addModel')}
                    </Button>
                }
            />

            <div className={cn(uiChrome.card, 'overflow-hidden')}>
                {models.length === 0 ? (
                    <div className="p-6">
                        <SettingsEmptyState
                            title={t('aiProvider.noModels')}
                            description={t('settings.ai.addModel')}
                        />
                    </div>
                ) : (
                    <div className="divide-y">
                        {models.map((model) => (
                            <div key={model.id} className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-muted/25">
                                <div className="min-w-0 flex items-center gap-3">
                                    <div className="rounded-full bg-primary/10 p-2 text-primary">
                                        {model.modelType === 'embedding' ? (
                                            <BrainCircuit className="w-4 h-4" />
                                        ) : (
                                            <Bot className="w-4 h-4" />
                                        )}
                                    </div>
                                    <div className="min-w-0">
                                        <div className="truncate font-medium text-foreground">{model.name}</div>
                                        <div className="mt-1 flex flex-wrap items-center gap-2">
                                            <SettingsBadge className="capitalize">{model.modelType}</SettingsBadge>
                                        </div>
                                    </div>
                                </div>
                                <Button
                                    type="button"
                                    onClick={() => setDeleteId(model.id)}
                                    variant="ghost"
                                    size="icon"
                                    className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
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
                                    className={cn(uiField.input, 'pr-28')}
                                />
                                <Button
                                    type="button"
                                    onClick={handleFetchModels}
                                    disabled={isFetching}
                                    variant="outline"
                                    size="sm"
                                    className="absolute bottom-1 right-1 top-1 h-auto"
                                >
                                    {isFetching ? <Search className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                                    {isFetching ? t('aiProvider.fetching') : t('aiProvider.fetchList')}
                                </Button>
                            </div>
                            {fetchError && (
                                <p className="text-xs text-destructive">{fetchError}</p>
                            )}
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium">{t('settings.ai.modelType', { defaultValue: 'Model Type' })}</label>
                            <select
                                value={newModelType}
                                onChange={(event) => setNewModelType(event.target.value as AiModelType)}
                                className={uiField.select}
                            >
                                <option value="llm">LLM</option>
                                <option value="embedding">Embedding</option>
                            </select>
                        </div>

                        {fetchedModels.length > 0 && (
                            <div className="max-h-40 space-y-2 overflow-y-auto rounded-[12px] border border-border/75 bg-muted/30 p-2">
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
                                            className={cn(
                                                uiChrome.control,
                                                'flex items-center justify-between px-2 py-2 text-left text-sm shadow-none transition-colors',
                                                newModelName === m.name ? 'border-primary/20 bg-primary/8 text-primary' : 'hover:bg-muted/60'
                                            )}
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
                        <Button
                            type="button"
                            onClick={handleCloseAdd}
                            variant="outline"
                        >
                            {t('actions.cancel')}
                        </Button>
                        <Button
                            type="button"
                            onClick={handleAddModel}
                            disabled={!newModelName || createMutation.isPending}
                        >
                            {createMutation.isPending ? t('messages.loading') : t('actions.add')}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <ConfirmDialog
                isOpen={!!deleteId}
                onCancel={() => setDeleteId(null)}
                onConfirm={handleDelete}
                title={
                    boundUsages.length > 0
                        ? t('settings.ai.deleteBoundModelTitle')
                        : t('actions.delete')
                }
                description={
                    boundUsages.length > 0
                        ? t('settings.ai.deleteBoundModelDescription', {
                            model: deletingModel?.name ?? '',
                            bindings: boundUsages.join('\n'),
                        })
                        : t('messages.confirmDelete')
                }
                confirmText={t('actions.delete')}
                variant="destructive"
                isLoading={deleteMutation.isPending}
            />
        </div>
    )
}
