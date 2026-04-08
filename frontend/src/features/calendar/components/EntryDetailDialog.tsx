import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Clock,
  Edit,
  ExternalLink,
  FileText,
  Hash,
  Loader2,
  Tag,
  Link2,
  Paperclip,
  X,
} from 'lucide-react'
import type { Entry } from '@/types'
import { getEntryRelations } from '@/features/relations/api/relations'
import { getEntryAttachments } from '@/features/attachments/api/attachments'
import { RelationList } from '@/features/relations/components/RelationList'
import { AttachmentList } from '@/features/attachments/components/AttachmentList'
import { remarkCitation } from '@/features/assistant/components/remark-citation'
import { CitationMarker } from '@/features/assistant/components/citation'
import { cn } from '@/lib/utils'
import { calendarRadius, calendarSurface } from '../styles'

interface EntryDetailDialogProps {
  entry: Entry | null
  open: boolean
  loading?: boolean
  onOpenChange: (open: boolean) => void
}

function withAlpha(color: string, opacity: number): string {
  const normalized = color.trim()
  const alphaHex = Math.round(Math.min(Math.max(opacity, 0), 1) * 255)
    .toString(16)
    .padStart(2, '0')

  if (/^#([0-9a-f]{3})$/i.test(normalized)) {
    const expanded = normalized
      .slice(1)
      .split('')
      .map((char) => char + char)
      .join('')
    return `#${expanded}${alphaHex}`
  }

  if (/^#([0-9a-f]{6}|[0-9a-f]{8})$/i.test(normalized)) {
    return `#${normalized.slice(1, 7)}${alphaHex}`
  }

  return `color-mix(in srgb, ${normalized} ${Math.round(opacity * 100)}%, transparent)`
}

function SectionCard({
  icon: Icon,
  title,
  count,
  children,
}: {
  icon: typeof Tag
  title: string
  count?: number
  children: React.ReactNode
}) {
  return (
    <section
      className={cn(
        'min-w-0 overflow-hidden p-3',
        calendarRadius.panel,
        calendarSurface.panel,
      )}
    >
      <div className="mb-2.5 flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground">
        <div
          className={cn(
            'flex h-8 w-8 items-center justify-center bg-muted/65 text-muted-foreground',
            calendarRadius.control,
          )}
        >
          <Icon className="h-4 w-4" />
        </div>
        <span>{title}</span>
        {typeof count === 'number' && (
          <span className="text-xs font-medium text-muted-foreground">
            ({count})
          </span>
        )}
      </div>
      {children}
    </section>
  )
}

function EmptyCard({ children }: { children: React.ReactNode }) {
  return (
    <div
      className={cn(
        'px-3.5 py-3 text-sm text-muted-foreground',
        calendarRadius.control,
        calendarSurface.inset,
      )}
    >
      {children}
    </div>
  )
}

function MetaLoading() {
  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3.5 py-3 text-sm text-muted-foreground',
        calendarRadius.control,
        calendarSurface.inset,
      )}
    >
      <Loader2 className="h-4 w-4 animate-spin" />
      <span>Loading...</span>
    </div>
  )
}

export function EntryDetailDialog({
  entry,
  open,
  loading = false,
  onOpenChange,
}: EntryDetailDialogProps) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS

  const { data: relations, isLoading: relationsLoading } = useQuery({
    queryKey: ['relations', 'entry', entry?.id],
    queryFn: () => getEntryRelations(entry!.id),
    enabled: !!entry?.id && open,
  })

  const { data: attachments, isLoading: attachmentsLoading } = useQuery({
    queryKey: ['attachments', 'entry', entry?.id],
    queryFn: () => getEntryAttachments(entry!.id),
    enabled: !!entry?.id && open,
  })

  if (!open) return null

  if (loading || !entry) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className={cn(
            'w-[min(calc(100vw-2rem),68rem)] max-h-[calc(100vh-2rem)] gap-0 overflow-hidden p-0',
            calendarRadius.shell,
            calendarSurface.dialog,
          )}
        >
          <div className="flex min-h-[16rem] items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  const handleEdit = () => {
    onOpenChange(false)
    navigate(`/entries/${entry.id}/edit`)
  }

  const handleViewDetails = () => {
    onOpenChange(false)
    navigate(`/entries/${entry.id}`)
  }

  const formatTime = () => {
    if (entry.timeMode === 'POINT' && entry.timeAt) {
      return format(new Date(entry.timeAt), 'PP p', { locale })
    }
    if (entry.timeMode === 'RANGE' && entry.timeFrom && entry.timeTo) {
      return `${format(new Date(entry.timeFrom), 'PP p', { locale })} - ${format(
        new Date(entry.timeTo),
        'PP p',
        { locale },
      )}`
    }
    return null
  }

  const timeString = formatTime()
  const typeColor = entry.type?.color || '#64748b'
  const tags = entry.tags ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          'w-[min(calc(100vw-2rem),68rem)] max-h-[calc(100vh-2rem)] gap-0 overflow-hidden p-0',
          calendarRadius.shell,
          calendarSurface.dialog,
        )}
      >
        <div className="border-b border-border/60 bg-background/88 px-4 py-3.5 backdrop-blur md:px-5">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="flex min-w-0 items-start gap-3.5">
              <div
                className={cn(
                  'flex h-11 w-11 shrink-0 items-center justify-center shadow-sm ring-1 ring-black/5',
                  calendarRadius.control,
                )}
                style={{
                  backgroundColor: withAlpha(typeColor, 0.12),
                  color: typeColor,
                }}
              >
                <span className="text-lg font-bold">
                  {entry.type?.name?.charAt(0) || 'E'}
                </span>
              </div>

              <div className="min-w-0">
                <DialogTitle className="text-[1.45rem] font-semibold leading-tight tracking-tight text-foreground md:text-[1.55rem]">
                  {entry.title}
                </DialogTitle>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {timeString && (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1.5 bg-muted/50 px-2.5 py-1 text-[13px] text-muted-foreground',
                        calendarRadius.pill,
                      )}
                    >
                      <Clock className="h-3.5 w-3.5" />
                      {timeString}
                    </span>
                  )}
                  {entry.type && (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1.5 px-2.5 py-1 text-[13px] font-medium',
                        calendarRadius.pill,
                      )}
                      style={{
                        backgroundColor: withAlpha(typeColor, 0.1),
                        color: typeColor,
                      }}
                    >
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: typeColor }}
                      />
                      {entry.type.name}
                    </span>
                  )}
                </div>
              </div>
            </div>

            <div
              className={cn(
                'inline-flex shrink-0 items-center bg-background/88 p-0.5',
                calendarRadius.control,
                calendarSurface.control,
              )}
            >
              <Button
                variant="ghost"
                size="icon"
                onClick={handleEdit}
                className={cn(
                  'h-8 w-8 text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                  calendarRadius.micro,
                )}
                aria-label={t('common.edit', 'Edit')}
              >
                <Edit className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleViewDetails}
                className={cn(
                  'h-8 w-8 text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                  calendarRadius.micro,
                )}
                aria-label={t('actions.openFullPage', 'Open full page')}
              >
                <ExternalLink className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onOpenChange(false)}
                className={cn(
                  'h-8 w-8 text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                  calendarRadius.micro,
                )}
                aria-label={t('actions.close', 'Close')}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>

        <ScrollArea className="max-h-[calc(100vh-7rem)] overflow-x-hidden overflow-y-auto">
          <div className="grid items-start gap-3 overflow-x-hidden p-3.5 lg:grid-cols-[minmax(0,1fr)_18rem] lg:px-4 lg:pb-4">
            <section
              className={cn(
                'min-w-0 p-4',
                calendarRadius.panel,
                calendarSurface.panel,
              )}
            >
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground">
                <div
                  className={cn(
                    'flex h-8 w-8 items-center justify-center bg-muted/65 text-muted-foreground',
                    calendarRadius.control,
                  )}
                >
                  <FileText className="h-4 w-4" />
                </div>
                <span>{t('labels.content', 'Details')}</span>
              </div>

              {entry.summary && (
                <div
                  className={cn(
                    'mb-3.5 px-3.5 py-3',
                    calendarRadius.control,
                    calendarSurface.inset,
                  )}
                >
                  <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/75">
                    {t('labels.summary', 'Summary')}
                  </div>
                  <p className="text-sm leading-6 text-foreground/85">
                    {entry.summary}
                  </p>
                </div>
              )}

              {entry.content ? (
                <div className="prose prose-sm max-w-none break-words text-foreground/85 dark:prose-invert prose-headings:tracking-tight prose-p:leading-7 prose-li:leading-7 prose-pre:rounded-2xl prose-pre:border prose-pre:border-border/60 prose-pre:bg-muted/35 prose-code:text-[0.92em]">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkCitation]}
                    components={
                      {
                        'citation-marker': ({
                          identifier,
                        }: {
                          identifier: string
                        }) => (
                          <CitationMarker
                            identifier={identifier}
                            label={identifier}
                          />
                        ),
                      } as any
                    }
                  >
                    {entry.content}
                  </ReactMarkdown>
                </div>
              ) : (
                <EmptyCard>
                  {t('labels.noContent', 'No additional content provided.')}
                </EmptyCard>
              )}
            </section>

            <div className="grid min-w-0 content-start gap-3">
              <SectionCard
                icon={Tag}
                title={t('labels.tags', 'Tags')}
                count={tags.length}
              >
                {tags.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {tags.map((tag) => (
                      <Badge
                        key={tag.id}
                        variant="secondary"
                        className={cn(
                          'border border-border/60 bg-muted/55 px-2.5 py-1 text-xs font-medium text-foreground/80',
                          calendarRadius.pill,
                        )}
                      >
                        {tag.name}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <EmptyCard>{t('entry.noTags', 'No tags yet')}</EmptyCard>
                )}
              </SectionCard>

              <SectionCard
                icon={Link2}
                title={t('labels.relations', 'Relations')}
                count={relations?.length ?? 0}
              >
                {relationsLoading ? (
                  <MetaLoading />
                ) : relations && relations.length > 0 ? (
                  <RelationList
                    relations={relations}
                    currentEntryId={entry.id}
                    compact
                  />
                ) : (
                  <EmptyCard>
                    {t('entry.noRelations', 'No linked records')}
                  </EmptyCard>
                )}
              </SectionCard>

              <SectionCard
                icon={Paperclip}
                title={t('labels.attachments', 'Attachments')}
                count={attachments?.length ?? 0}
              >
                {attachmentsLoading ? (
                  <MetaLoading />
                ) : attachments && attachments.length > 0 ? (
                  <AttachmentList attachments={attachments} compact />
                ) : (
                  <EmptyCard>
                    {t('entry.noAttachments', 'No files attached')}
                  </EmptyCard>
                )}
              </SectionCard>

              <SectionCard icon={Hash} title={t('labels.meta', 'Meta')}>
                <div
                  className={cn(
                    'space-y-1 px-1 text-[13px] leading-6 text-muted-foreground/88',
                    i18n.language === 'zh' ? 'tracking-[0.01em]' : '',
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/78">ID:</span>
                    <span className="font-mono text-foreground/68">
                      {entry.id.slice(0, 8)}...
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground/78">
                      {t('labels.updatedAt', 'Updated')}:
                    </span>
                    <span className="text-foreground/68">
                      {format(new Date(entry.updatedAt), 'PP', { locale })}
                    </span>
                  </div>
                </div>
              </SectionCard>
            </div>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
