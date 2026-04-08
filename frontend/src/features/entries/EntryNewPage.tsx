import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCreateEntryMutation } from './queries'
import { EntryForm } from './components/EntryForm'
import type { EntryUpsertRequest } from './api/entries'
import { useTranslation } from 'react-i18next'
import { uiChrome, uiLayout } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

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
    <div className={uiLayout.page6}>
      <div className={uiLayout.headerBlock}>
        <h1 className={uiLayout.headerTitle}>{t('pages.entryNew.title')}</h1>
      </div>
      <div className={cn(uiChrome.shell, 'p-5 md:p-6')}>
        <EntryForm
          defaultDate={defaultDate}
          onSubmit={handleSubmit}
          isSubmitting={createMutation.isPending}
        />
      </div>
    </div>
  )
}
