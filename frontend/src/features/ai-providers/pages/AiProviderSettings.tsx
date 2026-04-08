import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ModelBindingSection } from '../components/ModelBindingSection'
import { ProviderSidebar } from '../components/ProviderSidebar'
import { ProviderConfig } from '../components/ProviderConfig'
import { ProviderModels } from '../components/ProviderModels'
import { useCredentialsQuery, useCreateCredentialMutation } from '../queries'
import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import {
  SettingsEmptyState,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsWorkspace,
  SettingsWorkspaceContent,
  SettingsWorkspaceSidebar,
} from '@/features/settings/components/SettingsShell'

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
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('pages.aiProviders.title')}
          description={t('pages.aiProviders.description')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
        <SettingsSection className="flex min-h-[240px] items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </SettingsSection>
      </SettingsPageShell>
    )
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.aiProviders.title')}
        description={t('pages.aiProviders.description')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />

      <ModelBindingSection />

      <SettingsWorkspace
        sidebar={(
          <SettingsWorkspaceSidebar>
            <ProviderSidebar
              credentials={credentials}
              selectedId={selectedId}
              onSelect={handleSelect}
              onAdd={handleStartCreate}
              className="h-full"
            />
          </SettingsWorkspaceSidebar>
        )}
        content={(
          <SettingsWorkspaceContent>
            <div className="min-h-0 space-y-6 p-6">
              {isCreating ? (
                <SettingsSection className="space-y-6 p-6">
                  <div className="space-y-1">
                    <h2 className="text-lg font-semibold text-foreground">{t('aiProvider.addProvider')}</h2>
                    <p className="text-sm leading-6 text-muted-foreground">{t('aiProvider.addProviderDesc')}</p>
                  </div>
                  <div className="grid gap-4">
                    <div className="grid gap-2">
                      <label className="text-sm font-medium">{t('labels.name')}</label>
                      <input
                        type="text"
                        className={uiField.input}
                        placeholder="e.g. OpenRouter"
                        value={newProviderData.name}
                        onChange={e => setNewProviderData({ ...newProviderData, name: e.target.value })}
                      />
                    </div>
                    <div className="grid gap-2">
                      <label className="text-sm font-medium">Base URL</label>
                      <input
                        type="text"
                        className={uiField.input}
                        placeholder="https://..."
                        value={newProviderData.baseUrl}
                        onChange={e => setNewProviderData({ ...newProviderData, baseUrl: e.target.value })}
                      />
                    </div>
                    <div className="grid gap-2">
                      <label className="text-sm font-medium">API Key</label>
                      <input
                        type="password"
                        className={uiField.input}
                        placeholder="sk-..."
                        value={newProviderData.apiKey}
                        onChange={e => setNewProviderData({ ...newProviderData, apiKey: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-wrap justify-end gap-3 pt-2">
                      <Button type="button" variant="outline" onClick={() => setIsCreating(false)}>
                        {t('actions.cancel')}
                      </Button>
                      <Button
                        type="button"
                        onClick={handleCreateSubmit}
                        disabled={!newProviderData.name || !newProviderData.baseUrl || createMutation.isPending}
                      >
                        {createMutation.isPending ? t('messages.loading') : t('actions.add')}
                      </Button>
                    </div>
                  </div>
                </SettingsSection>
              ) : selectedCredential ? (
                <>
                  <SettingsSection className="space-y-6">
                    <ProviderConfig
                      credential={selectedCredential}
                      onDelete={() => setSelectedId(null)}
                    />
                  </SettingsSection>
                  <SettingsSection>
                    <ProviderModels credential={selectedCredential} />
                  </SettingsSection>
                </>
              ) : (
                <SettingsSection>
                  <SettingsEmptyState
                    title={t('aiProvider.selectProvider')}
                    description={t('pages.aiProviders.description')}
                  />
                </SettingsSection>
              )}
            </div>
          </SettingsWorkspaceContent>
        )}
      />
    </SettingsPageShell>
  )
}
