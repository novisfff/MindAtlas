import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Edit, Trash2, Calendar, Clock, Loader2, Link2, Paperclip } from 'lucide-react'
import { useEntryQuery, useDeleteEntryMutation, useEntryIndexStatusQuery } from './queries'
import { IndexStatusBadge } from './components/IndexStatusBadge'
import { cn } from '@/lib/utils'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { remarkCitation } from '@/features/assistant/components/remark-citation'
import { CitationMarker } from '@/features/assistant/components/citation'
import {
  RelationList,
  RelationSelector,
  SuggestedRelationList,
  useEntryRelationsQuery,
  useCreateRelationMutation,
  useDeleteRelationMutation,
} from '@/features/relations'
import {
  AttachmentList,
  FileUpload,
  useEntryAttachmentsQuery,
  useUploadAttachmentMutation,
  useDeleteAttachmentMutation,
  useRetryAttachmentParseMutation,
} from '@/features/attachments'

export function EntryDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: entry, isLoading, error } = useEntryQuery(id)
  const { data: indexStatus } = useEntryIndexStatusQuery(id)
  const deleteMutation = useDeleteEntryMutation()
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const { t } = useTranslation()

  // Relations
  const { data: relations = [] } = useEntryRelationsQuery(id || '')
  const createRelationMutation = useCreateRelationMutation()
  const deleteRelationMutation = useDeleteRelationMutation()

  // Attachments
  const { data: attachments = [] } = useEntryAttachmentsQuery(id || '')
  const uploadAttachmentMutation = useUploadAttachmentMutation(id || '')
  const deleteAttachmentMutation = useDeleteAttachmentMutation(id || '')
  const retryAttachmentParseMutation = useRetryAttachmentParseMutation(id || '')

  const formatDate = (dateString?: string) => {
    if (!dateString) return ''
    const date = new Date(dateString)
    if (isNaN(date.getTime())) return '—'
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })
  }

  const handleDelete = async () => {
    if (!id) return
    await deleteMutation.mutateAsync(id)
    navigate('/entries')
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
        <p className="text-destructive mb-4">{t('messages.failedToLoadEntry')}</p>
        <button
          onClick={() => navigate('/entries')}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          {t('actions.backToEntries')}
        </button>
      </div>
    )
  }

  const renderTimeInfo = () => {
    if (entry.timeMode === 'NONE') return null

    let timeText = ''
    if (entry.timeMode === 'POINT' && entry.timeAt) {
      timeText = formatDate(entry.timeAt)
    } else if (entry.timeMode === 'RANGE') {
      const from = entry.timeFrom ? formatDate(entry.timeFrom) : '?'
      const to = entry.timeTo ? formatDate(entry.timeTo) : '?'
      timeText = `${from} — ${to}`
    }

    if (!timeText) return null

    return (
      <div className="flex items-center text-sm text-muted-foreground">
        <Clock className="w-4 h-4 mr-2" />
        <span>{timeText}</span>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <button
          onClick={() => navigate('/entries')}
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          {t('actions.backToEntries')}
        </button>

        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(`/entries/${id}/edit`)}
            className={cn(
              'inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
              'border hover:bg-muted'
            )}
          >
            <Edit className="w-4 h-4 mr-1.5" />
            {t('actions.edit')}
          </button>

          <button
            onClick={() => setShowDeleteConfirm(true)}
            className={cn(
              'inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
              'border border-destructive/30 text-destructive hover:bg-destructive hover:text-destructive-foreground'
            )}
          >
            <Trash2 className="w-4 h-4 mr-1.5" />
            {t('actions.delete')}
          </button>
        </div>
      </div>

      <article className="bg-card rounded-lg border shadow-sm overflow-hidden">
        <div
          className="h-2"
          style={{ backgroundColor: entry.type?.color || '#cbd5e1' }}
        />

        <div className="p-6">
          <div className="flex items-start justify-between gap-4 mb-4">
            <h1 className="text-2xl font-bold">{entry.title}</h1>
            <span
              className="inline-flex items-center rounded-full border px-3 py-1 text-sm font-semibold shrink-0"
              style={{
                backgroundColor: entry.type?.color ? `${entry.type.color}20` : undefined,
                borderColor: entry.type?.color || undefined,
                color: entry.type?.color || undefined,
              }}
            >
              {entry.type?.name || t('labels.unknown')}
            </span>
          </div>

          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground mb-6">
            <div className="flex items-center">
              <Calendar className="w-4 h-4 mr-2" />
              <span>{t('labels.created')} {formatDate(entry.createdAt)}</span>
            </div>
            {renderTimeInfo()}
            {indexStatus && <IndexStatusBadge status={indexStatus.status} />}
          </div>

          {entry.tags && entry.tags.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-6">
              {entry.tags.map((tag) => (
                <span
                  key={tag.id}
                  className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border transition-colors"
                  style={{
                    backgroundColor: tag.color ? `${tag.color}15` : undefined,
                    borderColor: tag.color ? `${tag.color}40` : undefined,
                    color: tag.color || undefined,
                  }}
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full mr-1.5"
                    style={{ backgroundColor: tag.color || 'currentColor' }}
                  />
                  {tag.name}
                </span>
              ))}
            </div>
          )}

          {entry.summary && (
            <div className="mb-6 p-4 bg-muted/50 rounded-lg">
              <p className="text-sm font-medium mb-1">{t('labels.summary')}</p>
              <p className="text-muted-foreground">{entry.summary}</p>
            </div>
          )}

          {entry.content ? (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkCitation]}
                components={
                  {
                    'citation-marker': ({ identifier }: { identifier: string }) => (
                      <CitationMarker identifier={identifier} label={identifier} />
                    ),
                  } as any
                }
              >
                {entry.content}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="text-muted-foreground italic">{t('messages.noContent')}</p>
          )}
        </div>
      </article>

      {/* Relations Section */}
      <div className="mt-6 bg-card rounded-lg border shadow-sm p-6">
        <div className="flex items-center gap-2 mb-4">
          <Link2 className="w-5 h-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold">{t('labels.relations')}</h2>
        </div>

        <RelationList
          relations={relations}
          currentEntryId={id || ''}
          onDelete={(relationId) => deleteRelationMutation.mutate(relationId)}
          isDeleting={deleteRelationMutation.isPending}
        />

        {/* AI Suggestions */}
        <SuggestedRelationList entryId={id || ''} autoTrigger={relations.length === 0} />

        <div className="mt-4">
          <RelationSelector
            currentEntryId={id || ''}
            onAdd={(targetEntryId, relationTypeId) =>
              createRelationMutation.mutate({
                sourceEntryId: id || '',
                targetEntryId,
                relationTypeId,
              })
            }
            isAdding={createRelationMutation.isPending}
          />
        </div>
      </div>

      {/* Attachments Section */}
      <div className="mt-6 bg-card rounded-lg border shadow-sm p-6">
        <div className="flex items-center gap-2 mb-4">
          <Paperclip className="w-5 h-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold">{t('labels.attachments')}</h2>
        </div>

        <AttachmentList
          attachments={attachments}
          onDelete={(attachmentId) => deleteAttachmentMutation.mutate(attachmentId)}
          onRetry={(attachmentId) => retryAttachmentParseMutation.mutate(attachmentId)}
          isDeleting={deleteAttachmentMutation.isPending}
          isRetrying={retryAttachmentParseMutation.isPending}
        />

        <div className="mt-4">
          <FileUpload
            onUpload={(file, indexToKg) => uploadAttachmentMutation.mutate({ file, indexToKg })}
            isUploading={uploadAttachmentMutation.isPending}
          />
        </div>
      </div>

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="bg-card border rounded-lg shadow-lg p-6 max-w-md w-full mx-4">
            <h2 className="text-lg font-semibold mb-2">{t('actions.delete')}</h2>
            <p className="text-muted-foreground mb-6">
              {t('messages.deleteEntryConfirm', { title: entry.title })}
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 rounded-lg border hover:bg-muted transition-colors"
                disabled={deleteMutation.isPending}
              >
                {t('actions.cancel')}
              </button>
              <button
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className={cn(
                  'px-4 py-2 rounded-lg transition-colors',
                  'bg-destructive text-destructive-foreground hover:bg-destructive/90',
                  'disabled:opacity-50'
                )}
              >
                {deleteMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  t('actions.delete')
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
