import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ModelBindingSection } from '../components/ModelBindingSection'
import { ProviderSidebar } from '../components/ProviderSidebar'
import { ProviderConfig } from '../components/ProviderConfig'
import { ProviderModels } from '../components/ProviderModels'
import { useCredentialsQuery, useCreateCredentialMutation } from '../queries'
import { cn } from '@/lib/utils'

export function AiProviderSettings() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { data: credentials = [], isLoading } = useCredentialsQuery()
  const createMutation = useCreateCredentialMutation()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  // Create Form State
  const [newProviderData, setNewProviderData] = useState({ name: '', baseUrl: '', apiKey: '' })

  // Select first provider by default when loading finishes, if none selected
  useEffect(() => {
    if (!isLoading && credentials.length > 0 && !selectedId && !isCreating) {
      setSelectedId(credentials[0].id)
    }
  }, [isLoading, credentials, selectedId, isCreating])

  const handleSelect = (id: string) => {
    setSelectedId(id)
    setIsCreating(false)
  }

  const handleStartCreate = () => {
    setIsCreating(true)
    setSelectedId(null)
    setNewProviderData({ name: '', baseUrl: '', apiKey: '' })
  }

  const handleCreateSubmit = async () => {
    if (!newProviderData.name || !newProviderData.baseUrl) return

    const result = await createMutation.mutateAsync(newProviderData)
    // Assuming result returns the created object or we just switch to the new one
    // We'll rely on the query invalidation to refresh the list, and then we should select the new one.
    // Ideally createMutation returns the ID. If not, we might need to find it.
    // For now, let's just turn off creating, and let the useEffect or user select it.
    setIsCreating(false)
    // Optional: try to set selected ID if we get it back
    if ((result as any)?.id) {
      setSelectedId((result as any).id)
    }
  }

  const selectedCredential = credentials.find(c => c.id === selectedId)

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)] gap-4">
      {/* Header */}
      <div className="flex items-center gap-4 shrink-0">
        <button
          onClick={() => navigate('/settings')}
          className="inline-flex items-center justify-center w-8 h-8 rounded-full hover:bg-muted transition-colors"
        >
          <ArrowLeft className="w-5 h-5 text-muted-foreground" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">{t('pages.aiProviders.title')}</h1>
          <p className="text-muted-foreground text-sm">
            {t('pages.aiProviders.description')}
          </p>
        </div>
      </div>

      {/* Global Settings (Model Binding) */}
      <ModelBindingSection className="shrink-0" />

      {/* Main Content Area - Split Layout */}
      <div className="flex-1 border rounded-xl bg-card overflow-hidden flex shadow-sm">
        {/* Left Sidebar */}
        <div className="w-64 min-w-[16rem] h-full shrink-0">
          <ProviderSidebar
            credentials={credentials}
            selectedId={selectedId}
            onSelect={handleSelect}
            onAdd={handleStartCreate}
            className="h-full"
          />
        </div>

        {/* Right Content Panel */}
        <div className="flex-1 h-full overflow-y-auto bg-background/50 p-6">
          {isCreating ? (
            <div className="max-w-2xl mx-auto space-y-6">
              <div>
                <h2 className="text-lg font-medium">{t('aiProvider.addProvider')}</h2>
                <p className="text-sm text-muted-foreground">{t('aiProvider.addProviderDesc')}</p>
              </div>
              <div className="grid gap-4 p-6 border rounded-xl bg-card">
                <div className="grid gap-2">
                  <label className="text-sm font-medium">{t('labels.name')}</label>
                  <input type="text" className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="e.g. OpenRouter"
                    value={newProviderData.name}
                    onChange={e => setNewProviderData({ ...newProviderData, name: e.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-sm font-medium">Base URL</label>
                  <input type="text" className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="https://..."
                    value={newProviderData.baseUrl}
                    onChange={e => setNewProviderData({ ...newProviderData, baseUrl: e.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-sm font-medium">API Key</label>
                  <input type="password" className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="sk-..."
                    value={newProviderData.apiKey}
                    onChange={e => setNewProviderData({ ...newProviderData, apiKey: e.target.value })}
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <button onClick={() => setIsCreating(false)} className="px-4 py-2 text-sm hover:bg-muted rounded-md">{t('actions.cancel')}</button>
                  <button
                    onClick={handleCreateSubmit}
                    disabled={!newProviderData.name || !newProviderData.baseUrl || createMutation.isPending}
                    className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
                  >
                    {createMutation.isPending ? t('messages.loading') : t('actions.add')}
                  </button>
                </div>
              </div>
            </div>
          ) : selectedCredential ? (
            <div className="max-w-3xl mx-auto space-y-8 pb-10">
              <ProviderConfig
                credential={selectedCredential}
                onDelete={() => setSelectedId(null)}
              />

              <div className="my-6 border-t" />

              <ProviderModels credential={selectedCredential} />
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <p>{t('aiProvider.selectProvider')}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
