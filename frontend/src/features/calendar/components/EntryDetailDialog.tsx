import { useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Clock, Edit, ExternalLink, Hash, FileText, Loader2, Calendar as CalendarIcon, Tag, Link2, Paperclip, X } from 'lucide-react'
import type { Entry } from '@/types'
import { getEntryRelations } from '@/features/relations/api/relations'
import { getEntryAttachments } from '@/features/attachments/api/attachments'
import { RelationList } from '@/features/relations/components/RelationList'
import { AttachmentList } from '@/features/attachments/components/AttachmentList'
import { cn } from '@/lib/utils'

interface EntryDetailDialogProps {
  entry: Entry | null
  open: boolean
  loading?: boolean
  onOpenChange: (open: boolean) => void
}

export function EntryDetailDialog({ entry, open, loading = false, onOpenChange }: EntryDetailDialogProps) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS

  const { data: relations } = useQuery({
    queryKey: ['relations', 'entry', entry?.id],
    queryFn: () => getEntryRelations(entry!.id),
    enabled: !!entry?.id && open,
  })

  const { data: attachments } = useQuery({
    queryKey: ['attachments', 'entry', entry?.id],
    queryFn: () => getEntryAttachments(entry!.id),
    enabled: !!entry?.id && open,
  })

  if (!open) return null

  if (loading || !entry) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl min-h-[50vh] flex flex-col p-0 gap-0">
          <div className="flex-1 flex items-center justify-center">
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
      return `${format(new Date(entry.timeFrom), 'PP p', { locale })} - ${format(new Date(entry.timeTo), 'PP p', { locale })}`
    }
    return null
  }

  const timeString = formatTime()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col p-0 gap-0 overflow-hidden outline-none">

        {/* Header - Compact */}
        <div className="px-6 py-4 border-b bg-card z-10 sticky top-0">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
              style={{
                backgroundColor: entry.type?.color ? `${entry.type.color}15` : '#f3f4f6',
                color: entry.type?.color || '#6b7280'
              }}
            >
              {/* Placeholder icon if type icon is missing, or first letter */}
              <span className="text-lg font-bold">{entry.type?.name?.charAt(0) || 'E'}</span>
            </div>
            <div className="flex-1 min-w-0">
              <DialogTitle className="text-lg font-semibold leading-tight mb-1.5 line-clamp-2">
                {entry.title}
              </DialogTitle>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {timeString && (
                  <span className="flex items-center gap-1 bg-muted/50 px-1.5 py-0.5 rounded-sm">
                    <Clock className="w-3 h-3" />
                    {timeString}
                  </span>
                )}
                {entry.type && (
                  <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm"
                    style={{ color: entry.type.color }}
                  >
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: entry.type.color }} />
                    {entry.type.name}
                  </span>
                )}
              </div>
            </div>
            <div className="flex gap-2 shrink-0">
              <Button variant="ghost" size="icon" onClick={handleEdit} className="h-8 w-8" aria-label={t('common.edit', 'Edit')}>
                <Edit className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleViewDetails}
                className="h-8 w-8"
                aria-label={t('actions.openFullPage', 'Open full page')}
              >
                <ExternalLink className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onOpenChange(false)}
                className="h-8 w-8"
                aria-label={t('actions.close', 'Close')}
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          </div>
        </div>

        <ScrollArea className="flex-1">
          <div className="grid grid-cols-1 md:grid-cols-12 min-h-full divide-y md:divide-y-0 md:divide-x">

            {/* Main Content Area (Left) */}
            <div className="md:col-span-8 p-6 space-y-6">

              {/* Summary Section */}
              {entry.summary && (
                <div className="bg-muted/30 border rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    <FileText className="w-3.5 h-3.5" />
                    {t('labels.summary', 'Summary')}
                  </div>
                  <p className="text-sm leading-relaxed text-foreground/90">
                    {entry.summary}
                  </p>
                </div>
              )}

              {/* Main Text Content */}
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-foreground/80 flex items-center gap-2">
                  {t('labels.content', 'Details')}
                </h4>
                {entry.content ? (
                  <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/80">
                    <p className="whitespace-pre-wrap">{entry.content}</p>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground italic">
                    {t('labels.noContent', 'No additional content provided.')}
                  </p>
                )}
              </div>
            </div>

            {/* Sidebar (Right) - Metadata, Relations, Attachments */}
            <div className="md:col-span-4 bg-muted/10 p-5 space-y-6">

              {/* Tags */}
              <div className="space-y-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  <Tag className="w-3.5 h-3.5" />
                  {t('labels.tags', 'Tags')}
                </div>
                {entry.tags && entry.tags.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {entry.tags.map(tag => (
                      <Badge key={tag.id} variant="secondary" className="px-2 py-0.5 text-xs font-normal bg-secondary/50 hover:bg-secondary">
                        {tag.name}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">-</span>
                )}
              </div>

              <Separator className="bg-border/60" />

              {/* Relations */}
              <div className="space-y-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  <Link2 className="w-3.5 h-3.5" />
                  {t('labels.relations', 'Relations')} ({relations?.length || 0})
                </div>
                <div className="space-y-2">
                  {relations && relations.length > 0 ? (
                    <RelationList relations={relations} currentEntryId={entry.id} />
                  ) : (
                    <div className="text-xs text-muted-foreground italic pl-1">
                      {t('entry.noRelations', 'No linked records')}
                    </div>
                  )}
                </div>
              </div>

              <Separator className="bg-border/60" />

              {/* Attachments */}
              <div className="space-y-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  <Paperclip className="w-3.5 h-3.5" />
                  {t('labels.attachments', 'Attachments')} ({attachments?.length || 0})
                </div>
                <div className="space-y-2">
                  {attachments && attachments.length > 0 ? (
                    <AttachmentList attachments={attachments} />
                  ) : (
                    <div className="text-xs text-muted-foreground italic pl-1">
                      {t('entry.noAttachments', 'No files attached')}
                    </div>
                  )}
                </div>
              </div>

              <Separator className="bg-border/60" />

              <div className="pt-2 text-[10px] text-muted-foreground/60 space-y-1">
                <div>ID: <span className="font-mono">{entry.id.slice(0, 8)}...</span></div>
                <div>
                  {t('labels.updatedAt', 'Updated')}: {format(new Date(entry.updatedAt), 'PP', { locale })}
                </div>
              </div>

            </div>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
