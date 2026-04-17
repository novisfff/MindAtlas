import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Loader2, Plus, Pencil, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { getAgentProfile } from '../api/agents'
import {
  useAgentProfilesQuery,
  useCreateAgentProfileMutation,
  useDeleteAgentProfileMutation,
  useUpdateAgentProfileMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

export function AgentSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: agents = [], isLoading } = useAgentProfilesQuery()
  const createMutation = useCreateAgentProfileMutation()
  const updateMutation = useUpdateAgentProfileMutation()
  const deleteMutation = useDeleteAgentProfileMutation()

  const [isAdding, setIsAdding] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')

  const beginCreate = () => {
    setName('')
    setDescription('')
    setSystemPrompt('')
    setIsAdding(true)
    setEditingId(null)
  }

  const beginEdit = async (id: string) => {
    const row = agents.find((item) => item.id === id)
    if (!row) return
    try {
      const detail = row.detailsLoaded
        ? row
        : await qc.fetchQuery({
            queryKey: ['assistant-agent-profile', id],
            queryFn: () => getAgentProfile(id),
          })
      setName(detail.name)
      setDescription(detail.description)
      setSystemPrompt(detail.systemPrompt || '')
      setEditingId(id)
      setIsAdding(false)
    } catch (error) {
      const message = error instanceof Error ? error.message : t('common.error')
      toast.error(message)
    }
  }

  const submit = async () => {
    const trimmedName = name.trim()
    const trimmedPrompt = systemPrompt.trim()
    if (!trimmedName || !trimmedPrompt) return
    try {
      if (isAdding) {
        await createMutation.mutateAsync({
          name: trimmedName,
          description: description.trim(),
          systemPrompt: trimmedPrompt,
          tools: [],
          kbConfig: { enabled: false },
        })
        toast.success(t('settings.skills.agentCreated', { defaultValue: 'Agent created' }))
        setIsAdding(false)
      } else if (editingId) {
        await updateMutation.mutateAsync({
          id: editingId,
          data: {
            name: trimmedName,
            description: description.trim(),
            systemPrompt: trimmedPrompt,
          },
        })
        toast.success(t('settings.skills.agentUpdated', { defaultValue: 'Agent updated' }))
        setEditingId(null)
      }
      setName('')
      setDescription('')
      setSystemPrompt('')
    } catch (error) {
      const message = error instanceof Error ? error.message : t('common.error')
      toast.error(message)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button onClick={() => navigate('/settings')} className="p-2 rounded-lg hover:bg-muted">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">{t('pages.settings.assistantAgents', { defaultValue: 'Assistant Agents' })}</h1>
          <p className="text-muted-foreground">{t('pages.settings.assistantAgentsDesc', { defaultValue: 'Manage reusable agent-loop executables.' })}</p>
        </div>
      </div>

      <div className="bg-card rounded-xl border p-6 space-y-4">
        <div className="flex justify-between items-center">
          <h3 className="font-semibold">{t('settings.skills.agentList', { defaultValue: 'Agents' })}</h3>
          <button
            onClick={beginCreate}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
          >
            <Plus className="w-4 h-4" />
            {t('common.create')}
          </button>
        </div>

        {(isAdding || editingId) && (
          <div className="rounded-lg border p-4 space-y-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border bg-background"
              placeholder={t('settings.skills.name')}
            />
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border bg-background"
              placeholder={t('settings.skills.description')}
            />
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
              className="w-full px-3 py-2 rounded-lg border bg-background resize-none"
              placeholder={t('settings.skills.systemPrompt')}
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setIsAdding(false)
                  setEditingId(null)
                  setName('')
                  setDescription('')
                  setSystemPrompt('')
                }}
                className="px-3 py-1.5 rounded-lg border hover:bg-muted"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={submit}
                disabled={!name.trim() || !systemPrompt.trim() || createMutation.isPending || updateMutation.isPending}
                className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground disabled:opacity-50"
              >
                {t('common.save')}
              </button>
            </div>
          </div>
        )}

        {isLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
        ) : (
          <div className="space-y-2">
            {agents.map((item) => (
              <div key={item.id} className="rounded-lg border p-4 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{item.name}</div>
                  <div className="text-sm text-muted-foreground truncate">{item.description}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {t('settings.skills.referenceCount', { count: item.referenceCount, defaultValue: '{{count}} skills bound' })}
                  </div>
                </div>
                <button onClick={() => void beginEdit(item.id)} className="p-2 rounded hover:bg-muted">
                  <Pencil className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setDeleteId(item.id)}
                  disabled={item.referenceCount > 0 || item.isSystem}
                  className="p-2 rounded hover:bg-red-100 text-red-500 disabled:opacity-40"
                  title={item.referenceCount > 0 ? t('settings.skills.targetInUse', { defaultValue: 'In use by skills' }) : t('common.delete')}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={!!deleteId}
        title={t('settings.skills.deleteTitle')}
        description={t('settings.skills.deleteDescription')}
        onCancel={() => setDeleteId(null)}
        onConfirm={() => {
          if (!deleteId) return
          deleteMutation.mutate(deleteId, {
            onSuccess: () => setDeleteId(null),
            onError: (error) => {
              const message = error instanceof Error ? error.message : t('common.error')
              toast.error(message)
            },
          })
        }}
        isLoading={deleteMutation.isPending}
      />
    </div>
  )
}
