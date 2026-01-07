import { useNavigate } from 'react-router-dom'
import { useCreateEntryMutation } from './queries'
import { EntryForm } from './components/EntryForm'
import type { EntryUpsertRequest } from './api/entries'

export function EntryNewPage() {
  const navigate = useNavigate()
  const createMutation = useCreateEntryMutation()

  const handleSubmit = async (data: EntryUpsertRequest) => {
    const entry = await createMutation.mutateAsync(data)
    navigate(`/entries/${entry.id}`)
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">New Entry</h1>
      <EntryForm onSubmit={handleSubmit} isSubmitting={createMutation.isPending} />
    </div>
  )
}
