import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Save, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Entry } from '@/types'
import type { EntryUpsertRequest, EntryTimeMode } from '../api/entries'
import { TagSelector } from '@/features/tags/components/TagSelector'
import { cn } from '@/lib/utils'
import { useEntryForm } from './useEntryForm'
import { TagSuggestionPanel } from './TagSuggestionPanel'
import { AiContentEditor } from './AiContentEditor'
import { EntryTimeSection } from './EntryTimeSection'

const inputClass = cn(
  'w-full px-3 py-2 rounded-lg border bg-background',
  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
)

interface EntryFormProps {
  entry?: Entry
  defaultDate?: string
  onSubmit: (data: EntryUpsertRequest) => Promise<void>
  isSubmitting?: boolean
}

export function EntryForm({ entry, defaultDate, onSubmit, isSubmitting }: EntryFormProps) {
  const navigate = useNavigate()
  const { t } = useTranslation()

  const {
    title, setTitle,
    summary, setSummary,
    content, setContent,
    typeId, setTypeId,
    timeMode, setTimeMode,
    timeAt, setTimeAt,
    timeFrom, setTimeFrom,
    timeTo, setTimeTo,
    tagIds, setTagIds,
    prevContent,
    suggestedTags,
    isAiPending,
    entryTypes,
    selectedType,
    typesLoading,
    handleSubmit,
    handleAiGenerate,
    handleUndoContent,
    handleAddSuggestedTag,
  } = useEntryForm({ entry, defaultDate, onSubmit })

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          {t('actions.back')}
        </button>

        <button
          type="submit"
          disabled={isSubmitting || !title.trim() || !typeId}
          className={cn(
            'inline-flex items-center px-4 py-2 rounded-lg text-sm font-medium transition-colors',
            'bg-primary text-primary-foreground hover:bg-primary/90',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              {t('messages.saving')}
            </>
          ) : (
            <>
              <Save className="w-4 h-4 mr-2" />
              {entry ? t('actions.update') : t('actions.create')}
            </>
          )}
        </button>
      </div>

      <div className="space-y-4">
        <div>
          <label htmlFor="entry-title" className="block text-sm font-medium mb-1.5">
            {t('labels.title')} <span className="text-destructive">*</span>
          </label>
          <input
            id="entry-title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t('form.placeholder.title')}
            className={cn(inputClass, 'placeholder:text-muted-foreground')}
            autoFocus
          />
        </div>

        <div>
          <label htmlFor="entry-summary" className="block text-sm font-medium mb-1.5">
            {t('labels.summary')}
          </label>
          <textarea
            id="entry-summary"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder={t('labels.summary')}
            className={cn(inputClass, 'min-h-[80px] placeholder:text-muted-foreground resize-y')}
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="entry-type" className="block text-sm font-medium mb-1.5">
              {t('labels.type')} <span className="text-destructive">*</span>
            </label>
            <select
              id="entry-type"
              value={typeId}
              onChange={(e) => setTypeId(e.target.value)}
              disabled={typesLoading}
              className={inputClass}
            >
              {typesLoading ? (
                <option>{t('messages.loading')}</option>
              ) : (
                entryTypes.map((type) => (
                  <option key={type.id} value={type.id}>
                    {type.name}
                  </option>
                ))
              )}
            </select>
            {selectedType?.color && (
              <div
                className="mt-1 h-1 rounded-full"
                style={{ backgroundColor: selectedType.color }}
              />
            )}
          </div>

          <div>
            <label htmlFor="entry-time-mode" className="block text-sm font-medium mb-1.5">
              {t('entry.form.timeMode')}
            </label>
            <select
              id="entry-time-mode"
              value={timeMode}
              onChange={(e) => setTimeMode(e.target.value as EntryTimeMode)}
              className={inputClass}
            >
              <option value="POINT">{t('time.mode.point')}</option>
              <option value="RANGE">{t('time.mode.range')}</option>
            </select>
          </div>
        </div>

        <EntryTimeSection
          timeMode={timeMode}
          timeAt={timeAt}
          onTimeAtChange={setTimeAt}
          timeFrom={timeFrom}
          onTimeFromChange={setTimeFrom}
          timeTo={timeTo}
          onTimeToChange={setTimeTo}
        />

        <div>
          <label className="block text-sm font-medium mb-1.5">{t('labels.tags')}</label>
          <TagSelector value={tagIds} onChange={setTagIds} disabled={isSubmitting} allowCreate />
          <TagSuggestionPanel suggestedTags={suggestedTags} onAddTag={handleAddSuggestedTag} />
        </div>

        <AiContentEditor
          content={content}
          onContentChange={setContent}
          prevContent={prevContent}
          onUndoContent={handleUndoContent}
          onAiGenerate={handleAiGenerate}
          isAiPending={isAiPending}
          canGenerate={!!(title.trim() || content.trim())}
          disabled={isSubmitting}
        />
      </div>
    </form>
  )
}