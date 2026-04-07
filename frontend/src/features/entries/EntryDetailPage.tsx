import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import { ArrowLeft, Edit, Trash2, Calendar, Clock, Loader2, Link2, Paperclip } from 'lucide-react'
import { useEntryQuery, useDeleteEntryMutation, useEntryIndexStatusQuery } from './queries'
import { IndexStatusBadge } from './components/IndexStatusBadge'
import { cn } from '@/lib/utils'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { remarkCitation } from '@/features/assistant/components/remark-citation'
import { CitationMarker } from '@/features/assistant/components/citation'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
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
  useRetryAttachmentIndexMutation,
} from '@/features/attachments'
import { isApiError } from '@/lib/api/client'
import { useRuntimeConfigQuery } from '@/features/system-setup'
import { uiChrome, uiLayout, uiRadius } from '@/components/ui/styles'

export function EntryDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const { data: entry, isLoading, error } = useEntryQuery(id)
  const { data: indexStatus } = useEntryIndexStatusQuery(id)
  const deleteMutation = useDeleteEntryMutation()
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const { t } = useTranslation()
  const runtimeConfigQuery = useRuntimeConfigQuery()

  // Relations
  const { data: relations = [] } = useEntryRelationsQuery(id || '')
  const createRelationMutation = useCreateRelationMutation()
  const deleteRelationMutation = useDeleteRelationMutation()

  // Attachments
  const { data: attachments = [] } = useEntryAttachmentsQuery(id || '')
  const uploadAttachmentMutation = useUploadAttachmentMutation(id || '')
  const deleteAttachmentMutation = useDeleteAttachmentMutation(id || '')
  const retryAttachmentParseMutation = useRetryAttachmentParseMutation(id || '')
  const retryAttachmentIndexMutation = useRetryAttachmentIndexMutation(id || '')
  const attachmentsRef = useRef<HTMLDivElement>(null)
  const storageConfigured = runtimeConfigQuery.data ? Boolean(runtimeConfigQuery.data.storage.configured) : true
  const knowledgeGraphReady = runtimeConfigQuery.data
    ? Boolean(runtimeConfigQuery.data.knowledgeGraph.enabled && runtimeConfigQuery.data.knowledgeGraph.configured)
    : true
  const documentParsingReady = runtimeConfigQuery.data
    ? Boolean(runtimeConfigQuery.data.documentParsing.workerEnabled)
    : true

  useEffect(() => {
    if (location.hash === '#attachments' && attachmentsRef.current) {
      attachmentsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
      attachmentsRef.current.focus()
    }
  }, [location.hash])

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
    <div className={uiLayout.page6}>
      <div className={uiLayout.headerRow}>
        <button
          onClick={() => navigate('/entries')}
          className={uiLayout.backLink}
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          {t('actions.backToEntries')}
        </button>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => navigate(`/entries/${id}/edit`)}
          >
            <Edit className="w-4 h-4 mr-1.5" />
            {t('actions.edit')}
          </Button>

          <Button
            variant="destructive"
            onClick={() => setShowDeleteConfirm(true)}
          >
            <Trash2 className="w-4 h-4 mr-1.5" />
            {t('actions.delete')}
          </Button>
        </div>
      </div>

      <article className={cn(uiChrome.shell, 'overflow-hidden')}>
        <div
          className="h-2"
          style={{ backgroundColor: entry.type?.color || '#cbd5e1' }}
        />

        <div className="p-6">
          <div className="flex items-start justify-between gap-4 mb-4">
            <h1 className="text-2xl font-bold">{entry.title}</h1>
            <Badge
              variant="outline"
              className="shrink-0 px-3 py-1 text-sm font-semibold"
              style={{
                backgroundColor: entry.type?.color ? `${entry.type.color}20` : undefined,
                borderColor: entry.type?.color || undefined,
                color: entry.type?.color || undefined,
              }}
            >
              {entry.type?.name || t('labels.unknown')}
            </Badge>
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
                  className={cn(
                    uiRadius.pill,
                    'inline-flex items-center border px-2.5 py-0.5 text-xs font-medium transition-colors',
                  )}
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
            <div className={cn(uiChrome.inset, 'mb-6 p-4')}>
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
      <div className={cn(uiChrome.card, 'p-6')}>
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
      <div
        ref={attachmentsRef}
        id="attachments"
        tabIndex={-1}
        className={cn(uiChrome.card, 'p-6 focus:outline-none')}
      >
        <div className="flex items-center gap-2 mb-4">
          <Paperclip className="w-5 h-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold">{t('labels.attachments')}</h2>
        </div>

        <AttachmentList
          attachments={attachments}
          onDelete={(attachmentId) => deleteAttachmentMutation.mutate(attachmentId)}
          onRetry={(attachmentId) =>
            retryAttachmentParseMutation.mutate(attachmentId, {
              onError: (error) => {
                toast.error(error instanceof Error ? error.message : t('messages.error'))
              },
            })
          }
          onRetryIndex={(attachmentId) =>
            retryAttachmentIndexMutation.mutate(attachmentId, {
              onError: (error) => {
                toast.error(error instanceof Error ? error.message : t('messages.error'))
              },
            })
          }
          isDeleting={deleteAttachmentMutation.isPending}
          isRetrying={retryAttachmentParseMutation.isPending}
          isRetryingIndex={retryAttachmentIndexMutation.isPending}
        />

        <div className="mt-4">
          {!storageConfigured ? (
            <div className="rounded-[24px] border border-amber-200 bg-amber-50 px-4 py-4">
              <p className="text-sm font-semibold text-amber-900">{t('systemSetup.emptyStates.storageTitle')}</p>
              <p className="mt-2 text-sm leading-6 text-amber-800">{t('systemSetup.emptyStates.storageUnconfigured')}</p>
            </div>
          ) : (
            <div className="space-y-3">
              <FileUpload
                onUpload={(file, indexToKg) =>
                  uploadAttachmentMutation.mutate(
                    { file, indexToKg },
                    {
                      onError: (error) => {
                        if (isApiError(error)) {
                          if (error.code === 40981) {
                            toast.error(t('systemSetup.emptyStates.storageUnconfigured'))
                            return
                          }
                          if (error.code === 40982) {
                            toast.error(t('systemSetup.emptyStates.documentParsingUnavailable'))
                            return
                          }
                          if (error.code === 40983 || error.code === 40984) {
                            toast.error(t('systemSetup.emptyStates.knowledgeGraphIncomplete'))
                            return
                          }
                        }
                        toast.error(error instanceof Error ? error.message : t('messages.error'))
                      },
                    }
                  )
                }
                isUploading={uploadAttachmentMutation.isPending}
              />

              {(!documentParsingReady || !knowledgeGraphReady) ? (
                <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm leading-6 text-slate-600">
                  {!documentParsingReady ? t('systemSetup.emptyStates.documentParsingUnavailable') : t('systemSetup.emptyStates.knowledgeGraphIncomplete')}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title={t('actions.delete')}
        description={t('messages.deleteEntryConfirm', { title: entry.title })}
        confirmText={t('actions.delete')}
        cancelText={t('actions.cancel')}
        variant="destructive"
        isLoading={deleteMutation.isPending}
        onConfirm={() => {
          void handleDelete()
        }}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  )
}
