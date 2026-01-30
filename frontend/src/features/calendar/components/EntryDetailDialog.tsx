import { useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Clock, Edit, ExternalLink, Hash, FileText } from 'lucide-react'
import type { Entry } from '@/types'

interface EntryDetailDialogProps {
  entry: Entry | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function EntryDetailDialog({ entry, open, onOpenChange }: EntryDetailDialogProps) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS

  if (!entry) return null

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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="p-6 pb-4">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Badge
                  variant="outline"
                  style={{
                    backgroundColor: entry.type?.color ? `${entry.type.color}15` : undefined,
                    color: entry.type?.color,
                    borderColor: entry.type?.color ? `${entry.type.color}40` : undefined
                  }}
                >
                  {entry.type?.name}
                </Badge>
                {entry.tags?.map(tag => (
                  <Badge key={tag.id} variant="secondary" className="text-xs">
                    <Hash className="w-3 h-3 mr-1 opacity-50" />
                    {tag.name}
                  </Badge>
                ))}
              </div>
              <DialogTitle className="text-xl leading-relaxed">{entry.title}</DialogTitle>
            </div>
          </div>
        </DialogHeader>

        <Separator />

        <ScrollArea className="flex-1 p-6">
          <div className="space-y-6">
            {/* Time Information */}
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Clock className="w-4 h-4" />
              <span>{formatTime()}</span>
            </div>

            {/* Summary or Content Snippet */}
            <div className="space-y-3">
              {entry.summary && (
                <div className="p-4 rounded-lg bg-muted/50 text-sm leading-relaxed">
                  <div className="flex items-center gap-2 mb-2 text-muted-foreground font-medium">
                    <FileText className="w-4 h-4" />
                    {t('labels.summary', 'Summary')}
                  </div>
                  {entry.summary}
                </div>
              )}

              {!entry.summary && entry.content && (
                <div className="text-sm text-muted-foreground line-clamp-6">
                  {entry.content}
                </div>
              )}
            </div>
          </div>
        </ScrollArea>

        <Separator />

        <DialogFooter className="p-4 sm:justify-between">
          <div className="text-xs text-muted-foreground flex items-center">
            {t('labels.updatedAt', 'Updated')}: {format(new Date(entry.updatedAt), 'PP', { locale })}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleEdit}>
              <Edit className="w-4 h-4 mr-2" />
              {t('actions.edit', 'Edit')}
            </Button>
            <Button onClick={handleViewDetails}>
              <ExternalLink className="w-4 h-4 mr-2" />
              {t('citation.viewEntry', 'View Details')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
