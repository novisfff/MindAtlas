import { Check, Loader2, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { tagsKeys, useCreateTagMutation, useTagsQuery } from '../queries'
import { isApiError } from '@/lib/api/client'
import { getColorByName } from '@/lib/colors'
import { cn } from '@/lib/utils'

interface TagSelectorProps {
  value: string[]
  onChange: (value: string[]) => void
  disabled?: boolean
  allowCreate?: boolean
}

function normalizeTagName(raw: string): string {
  const stripped = raw.trim().replace(/^#+/, '').trim()
  if (!stripped) return ''
  if (stripped.length <= 128) return stripped
  return stripped.slice(0, 128).trim()
}

export function TagSelector({ value, onChange, disabled, allowCreate }: TagSelectorProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const queryClient = useQueryClient()
  const { data: tags = [], isLoading, refetch } = useTagsQuery()
  const createTagMutation = useCreateTagMutation()
  const isBusy = !!disabled || createTagMutation.isPending

  const toggleTag = (tagId: string) => {
    if (isBusy) return
    const next = value.includes(tagId)
      ? value.filter(id => id !== tagId)
      : [...value, tagId]
    onChange(next)
  }

  const addTag = (tagId: string) => {
    if (isBusy) return
    if (value.includes(tagId)) return
    onChange([...value, tagId])
  }

  const normalizedQuery = allowCreate ? normalizeTagName(query) : ''

  const exactMatch = useMemo(() => {
    if (!normalizedQuery) return null
    const key = normalizedQuery.toLowerCase()
    return tags.find((t) => t.name.toLowerCase() === key) ?? null
  }, [normalizedQuery, tags])

  const visibleTags = useMemo(() => {
    if (!allowCreate) return tags
    if (!normalizedQuery) return tags
    const key = normalizedQuery.toLowerCase()
    return tags.filter((t) => t.name.toLowerCase().includes(key))
  }, [allowCreate, normalizedQuery, tags])

  const handleCreateOrSelect = async () => {
    if (!allowCreate) return
    if (isBusy) return
    if (!normalizedQuery) return

    if (exactMatch) {
      addTag(exactMatch.id)
      setQuery('')
      return
    }

    try {
      const newTag = await createTagMutation.mutateAsync({
        name: normalizedQuery,
        color: getColorByName(normalizedQuery),
      })
      queryClient.setQueryData(tagsKeys.list(), (old: unknown) => {
        const list = Array.isArray(old) ? (old as { id: string }[]) : []
        if (list.some((t) => t.id === newTag.id)) return list
        return [...list, newTag]
      })
      addTag(newTag.id)
      setQuery('')
      return
    } catch (error) {
      if (isApiError(error) && error.code === 40001) {
        const refreshed = await refetch()
        const existing = (refreshed.data ?? []).find(
          (t) => t.name.toLowerCase() === normalizedQuery.toLowerCase()
        )
        if (existing) {
          addTag(existing.id)
          setQuery('')
          return
        }
      }
      // Keep query so user can retry or edit.
      console.error('Failed to create tag:', error)
    }
  }

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">{t('messages.loading')}</div>
  }

  return (
    <div className="space-y-2">
      {allowCreate && (
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void handleCreateOrSelect()
            }
          }}
          placeholder={t('tags.searchPlaceholder')}
          disabled={isBusy}
          className={cn(
            'w-full px-3 py-2 rounded-lg border bg-background text-sm',
            'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
            isBusy && 'opacity-50 cursor-not-allowed'
          )}
        />
      )}

      <div className="flex flex-wrap gap-2">
        {allowCreate && normalizedQuery && !exactMatch && (
          <button
            type="button"
            onClick={() => void handleCreateOrSelect()}
            disabled={isBusy}
            className={cn(
              'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm border transition-colors',
              'border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              isBusy && 'opacity-50 cursor-not-allowed'
            )}
          >
            {createTagMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {t('tags.create', { name: normalizedQuery })}
          </button>
        )}

        {visibleTags.map(tag => {
        const selected = value.includes(tag.id)
        return (
          <button
            key={tag.id}
            type="button"
            aria-pressed={selected}
            onClick={() => toggleTag(tag.id)}
            disabled={isBusy}
            className={cn(
              'inline-flex items-center px-3 py-1 rounded-full text-sm border transition-colors',
              selected
                ? 'border-transparent bg-primary/10 text-primary hover:bg-primary/20'
                : 'border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              isBusy && 'opacity-50 cursor-not-allowed'
            )}
            style={
              selected && tag.color
                ? { backgroundColor: tag.color + '20', color: tag.color, borderColor: tag.color }
                : undefined
            }
          >
            {tag.name}
            {selected && <Check className="ml-1.5 h-3.5 w-3.5" aria-hidden="true" />}
          </button>
        )
      })}
        {visibleTags.length === 0 && !(allowCreate && normalizedQuery) && (
          <span className="text-sm text-muted-foreground italic">{t('messages.noTags')}</span>
        )}
      </div>
    </div>
  )
}
