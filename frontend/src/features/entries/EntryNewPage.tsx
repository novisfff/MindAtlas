import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCreateEntryMutation } from './queries'
import { EntryForm } from './components/EntryForm'
import type { EntryUpsertRequest } from './api/entries'
import { useTranslation } from 'react-i18next'

export function EntryNewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const createMutation = useCreateEntryMutation()
  const { t } = useTranslation()

  const defaultDate = searchParams.get('date') || undefined

  const handleSubmit = async (data: EntryUpsertRequest) => {
    const entry = await createMutation.mutateAsync(data)
    navigate(`/entries/${entry.id}`)
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">{t('pages.entryNew.title')}</h1>
      <EntryForm
        defaultDate={defaultDate}
        onSubmit={handleSubmit}
        isSubmitting={createMutation.isPending}
      />
    </div>
  )
}
