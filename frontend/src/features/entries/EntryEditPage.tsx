import { useNavigate, useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useEntryQuery, useUpdateEntryMutation } from './queries'
import { EntryForm } from './components/EntryForm'
import type { EntryUpsertRequest } from './api/entries'

export function EntryEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: entry, isLoading, error } = useEntryQuery(id)
  const updateMutation = useUpdateEntryMutation()

  const handleSubmit = async (data: EntryUpsertRequest) => {
    if (!id) return
    await updateMutation.mutateAsync({ id, payload: data })
    navigate(`/entries/${id}`)
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !entry) {
    return (
      <div className="text-center py-16">
        <p className="text-destructive mb-4">Failed to load entry</p>
        <button
          onClick={() => navigate('/entries')}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          Back to entries
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Edit Entry</h1>
      <EntryForm
        entry={entry}
        onSubmit={handleSubmit}
        isSubmitting={updateMutation.isPending}
      />
    </div>
  )
}
