import { useState, useEffect, FormEvent } from 'react'
import type { Entry } from '@/types'
import type { EntryUpsertRequest, EntryTimeMode } from '../api/entries'
import { useEntryTypesQuery } from '@/features/entry-types/queries'
import { useTagsQuery, useCreateTagMutation } from '@/features/tags/queries'
import { useAiGenerateMutation } from '@/features/ai'
import { getColorByName } from '@/lib/colors'

interface UseEntryFormProps {
  entry?: Entry
  defaultDate?: string
  onSubmit: (data: EntryUpsertRequest) => Promise<void>
}

export function useEntryForm({ entry, defaultDate, onSubmit }: UseEntryFormProps) {
  const { data: entryTypes = [], isLoading: typesLoading } = useEntryTypesQuery()
  const { data: allTags = [] } = useTagsQuery()
  const createTagMutation = useCreateTagMutation()
  const aiMutation = useAiGenerateMutation()

  const [title, setTitle] = useState(entry?.title ?? '')
  const [summary, setSummary] = useState(entry?.summary ?? '')
  const [content, setContent] = useState(entry?.content ?? '')
  const [typeId, setTypeId] = useState(entry?.type?.id ?? '')
  const [timeMode, setTimeMode] = useState<EntryTimeMode>(
    (entry?.timeMode && entry.timeMode !== 'NONE') ? entry.timeMode : 'POINT'
  )
  const [timeAt, setTimeAt] = useState(
    entry?.timeAt?.split('T')[0] ?? defaultDate ?? new Date().toISOString().split('T')[0]
  )
  const [timeFrom, setTimeFrom] = useState(entry?.timeFrom?.split('T')[0] ?? defaultDate ?? '')
  const [timeTo, setTimeTo] = useState(entry?.timeTo?.split('T')[0] ?? '')
  const [tagIds, setTagIds] = useState<string[]>(entry?.tags?.map(t => t.id) ?? [])

  const [prevContent, setPrevContent] = useState<string | null>(null)
  const [suggestedTags, setSuggestedTags] = useState<string[]>([])

  useEffect(() => {
    if (!typeId && entryTypes.length > 0) {
      setTypeId(entryTypes[0].id)
    }
  }, [entryTypes, typeId])

  const selectedType = entryTypes.find((t) => t.id === typeId)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !typeId) return

    const payload: EntryUpsertRequest = {
      title: title.trim(),
      summary: summary.trim() || undefined,
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
            setSummary(data.summary)
          }
          if (data.refinedContent) {
            setPrevContent(content)
            setContent(data.refinedContent)
          }
          if (data.suggestedTags.length > 0) {
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
    const existingTag = allTags.find((t) => t.name.toLowerCase() === tagName.toLowerCase())

    if (existingTag) {
      setTagIds((prev) => [...prev, existingTag.id])
    } else {
      try {
        const newTag = await createTagMutation.mutateAsync({ name: tagName, color: getColorByName(tagName) })
        setTagIds((prev) => [...prev, newTag.id])
      } catch (error) {
        console.error('Failed to create tag:', error)
      }
    }
    setSuggestedTags((prev) => prev.filter((t) => t !== tagName))
  }

  return {
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
    isAiPending: aiMutation.isPending,
    entryTypes,
    selectedType,
    typesLoading,
    handleSubmit,
    handleAiGenerate,
    handleUndoContent,
    handleAddSuggestedTag,
  }
}
