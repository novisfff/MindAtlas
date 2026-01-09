import { FormEvent, useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Save, Loader2, Undo, Plus } from 'lucide-react'
import type { Entry, EntryType } from '@/types'
import type { EntryUpsertRequest, EntryTimeMode } from '../api/entries'
import { useEntryTypesQuery } from '@/features/entry-types/queries'
import { TagSelector } from '@/features/tags/components/TagSelector'
import { MarkdownEditor } from './MarkdownEditor'
import { cn } from '@/lib/utils'
import { AiAssistButton, useAiGenerateMutation } from '@/features/ai'
import { useTagsQuery, useCreateTagMutation } from '@/features/tags/queries'

interface EntryFormProps {
  entry?: Entry
  onSubmit: (data: EntryUpsertRequest) => Promise<void>
  isSubmitting?: boolean
}

export function EntryForm({ entry, onSubmit, isSubmitting }: EntryFormProps) {
  const navigate = useNavigate()
  const { data: entryTypes = [], isLoading: typesLoading } = useEntryTypesQuery()
  const { data: allTags = [] } = useTagsQuery()
  const createTagMutation = useCreateTagMutation()
  const aiMutation = useAiGenerateMutation()

  const [title, setTitle] = useState(entry?.title ?? '')
  const [content, setContent] = useState(entry?.content ?? '')
  const [typeId, setTypeId] = useState(entry?.type?.id ?? '')
  const [timeMode, setTimeMode] = useState<EntryTimeMode>(entry?.timeMode ?? 'NONE')
  const [timeAt, setTimeAt] = useState(entry?.timeAt?.split('T')[0] ?? '')
  const [timeFrom, setTimeFrom] = useState(entry?.timeFrom?.split('T')[0] ?? '')
  const [timeTo, setTimeTo] = useState(entry?.timeTo?.split('T')[0] ?? '')
  const [tagIds, setTagIds] = useState<string[]>(entry?.tags?.map(t => t.id) ?? [])

  // AI state
  const [prevContent, setPrevContent] = useState<string | null>(null)
  const [suggestedTags, setSuggestedTags] = useState<string[]>([])



  useEffect(() => {
    if (!typeId && entryTypes.length > 0) {
      setTypeId(entryTypes[0].id)
    }
  }, [entryTypes, typeId])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !typeId) return

    const payload: EntryUpsertRequest = {
      title: title.trim(),
      content: content.trim() || undefined,
      typeId,
      timeMode,
      timeAt: timeMode === 'POINT' && timeAt ? `${timeAt}T00:00:00Z` : undefined,
      timeFrom: timeMode === 'RANGE' && timeFrom ? `${timeFrom}T00:00:00Z` : undefined,
      timeTo: timeMode === 'RANGE' && timeTo ? `${timeTo}T23:59:59Z` : undefined,
      tagIds,
    }

    await onSubmit(payload)
  }

  const selectedType = entryTypes.find((t) => t.id === typeId)

  const handleAiGenerate = () => {
    if (!title.trim() && !content.trim()) return

    aiMutation.mutate(
      {
        title: title.trim(),
        content: content.trim(),
        typeName: selectedType?.name ?? '',
      },
      {
        onSuccess: (data) => {
          if (data.summary) {
            setPrevContent(content)
            setContent(data.summary)
          }

          if (data.suggestedTags.length > 0) {
            const newSuggestions = data.suggestedTags.filter(
              (tagName) => !allTags.find((t) => t.id === tagName && tagIds.includes(t.id)) && !tagIds.includes(allTags.find(t => t.name === tagName)?.id ?? '')
            )
            // Filter out tags that are already selected by name
            const filteredSuggestions = data.suggestedTags.filter(tagName => {
              const tag = allTags.find(t => t.name === tagName)
              return !tag || !tagIds.includes(tag.id)
            })
            setSuggestedTags(filteredSuggestions)
          }
        },
      }
    )
  }

  const handleUndoContent = () => {
    if (prevContent !== null) {
      setContent(prevContent)
      setPrevContent(null)
    }
  }

  const handleAddSuggestedTag = async (tagName: string) => {
    // 1. Try to find existing tag (case-insensitive)
    const existingTag = allTags.find((t) => t.name.toLowerCase() === tagName.toLowerCase())

    if (existingTag) {
      setTagIds((prev) => [...prev, existingTag.id])
    } else {
      // 2. Create new tag if it doesn't exist
      try {
        const newTag = await createTagMutation.mutateAsync({ name: tagName })
        setTagIds((prev) => [...prev, newTag.id])
      } catch (error) {
        console.error('Failed to create tag:', error)
        // Optionally show a toast error here
      }
    }
    // Always remove from suggestions to give feedback
    setSuggestedTags((prev) => prev.filter((t) => t !== tagName))
  }



  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back
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
              Saving...
            </>
          ) : (
            <>
              <Save className="w-4 h-4 mr-2" />
              {entry ? 'Update' : 'Create'}
            </>
          )}
        </button>
      </div>

      <div className="space-y-4">
        <div>
          <label htmlFor="entry-title" className="block text-sm font-medium mb-1.5">
            Title <span className="text-destructive">*</span>
          </label>
          <input
            id="entry-title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Enter a title..."
            className={cn(
              'w-full px-3 py-2 rounded-lg border bg-background',
              'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
              'placeholder:text-muted-foreground'
            )}
            autoFocus
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="entry-type" className="block text-sm font-medium mb-1.5">
              Type <span className="text-destructive">*</span>
            </label>
            <select
              id="entry-type"
              value={typeId}
              onChange={(e) => setTypeId(e.target.value)}
              disabled={typesLoading}
              className={cn(
                'w-full px-3 py-2 rounded-lg border bg-background',
                'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
              )}
            >
              {typesLoading ? (
                <option>Loading...</option>
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
              Time Mode
            </label>
            <select
              id="entry-time-mode"
              value={timeMode}
              onChange={(e) => setTimeMode(e.target.value as EntryTimeMode)}
              className={cn(
                'w-full px-3 py-2 rounded-lg border bg-background',
                'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
              )}
            >
              <option value="NONE">No time</option>
              <option value="POINT">Point in time</option>
              <option value="RANGE">Time range</option>
            </select>
          </div>
        </div>

        {timeMode === 'POINT' && (
          <div>
            <label htmlFor="entry-time-at" className="block text-sm font-medium mb-1.5">
              Date
            </label>
            <input
              id="entry-time-at"
              type="date"
              value={timeAt}
              onChange={(e) => setTimeAt(e.target.value)}
              className={cn(
                'w-full px-3 py-2 rounded-lg border bg-background',
                'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
              )}
            />
          </div>
        )}

        {timeMode === 'RANGE' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="entry-time-from" className="block text-sm font-medium mb-1.5">
                From
              </label>
              <input
                id="entry-time-from"
                type="date"
                value={timeFrom}
                onChange={(e) => setTimeFrom(e.target.value)}
                className={cn(
                  'w-full px-3 py-2 rounded-lg border bg-background',
                  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
                )}
              />
            </div>
            <div>
              <label htmlFor="entry-time-to" className="block text-sm font-medium mb-1.5">
                To
              </label>
              <input
                id="entry-time-to"
                type="date"
                value={timeTo}
                onChange={(e) => setTimeTo(e.target.value)}
                className={cn(
                  'w-full px-3 py-2 rounded-lg border bg-background',
                  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
                )}
              />
            </div>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium mb-1.5">Tags</label>
          <TagSelector value={tagIds} onChange={setTagIds} disabled={isSubmitting} />
          {suggestedTags.length > 0 && (
            <div className="mt-2 text-sm animate-in fade-in slide-in-from-top-1">
              <span className="text-muted-foreground mr-2">AI Suggestions:</span>
              <div className="inline-flex flex-wrap gap-1.5">
                {suggestedTags.map((tagName) => (
                  <button
                    key={tagName}
                    type="button"
                    onClick={() => handleAddSuggestedTag(tagName)}
                    className={cn(
                      "inline-flex items-center px-2 py-0.5 rounded-full text-xs transition-colors",
                      "bg-purple-100 text-purple-700 hover:bg-purple-200",
                      "dark:bg-purple-900/30 dark:text-purple-300 dark:hover:bg-purple-900/50"
                    )}
                  >
                    <Plus className="w-3 h-3 mr-1" />
                    {tagName}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="block text-sm font-medium">Content</label>
            <div className="flex items-center gap-3">
              {prevContent !== null && (
                <button
                  type="button"
                  onClick={handleUndoContent}
                  className="text-xs flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Undo className="w-3 h-3" />
                  Undo Change
                </button>
              )}
              <AiAssistButton
                onClick={handleAiGenerate}
                isLoading={aiMutation.isPending}
                disabled={isSubmitting || (!title.trim() && !content.trim())}
              />
            </div>
          </div>
          <MarkdownEditor value={content} onChange={setContent} disabled={isSubmitting} />

          {/* AI Suggestions Panel */}

        </div>
      </div>
    </form>
  )
}
