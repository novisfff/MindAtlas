import { useNavigate } from 'react-router-dom'
import { FileText, Network, ArrowRight, Paperclip } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { CitationData } from './utils'

interface CitationHoverCardProps {
  citation: CitationData
}

/**
 * Entry 引用预览卡片
 */
function EntryPreview({ citation }: CitationHoverCardProps) {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const data = citation.sourceData

  const handleClick = () => {
    if (data?.entryId) {
      navigate(`/entries/${data.entryId}`)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-primary">
        <FileText className="h-4 w-4" />
        <span className="text-xs font-medium">{t('citation.entry', 'Entry')}</span>
      </div>

      {data?.title && (
        <h4 className="font-medium text-sm line-clamp-2">{data.title}</h4>
      )}

      {data?.summary && (
        <p className="text-xs text-muted-foreground line-clamp-3">
          {data.summary}
        </p>
      )}

      {data?.entryId && (
        <button
          onClick={handleClick}
          className={cn(
            "flex items-center gap-1 text-xs text-primary hover:underline",
            "mt-2 pt-2 border-t border-border/50"
          )}
        >
          {t('citation.viewEntry', 'View Entry')}
          <ArrowRight className="h-3 w-3" />
        </button>
      )}

      {!data && (
        <p className="text-xs text-muted-foreground italic">
          {t('citation.notFound', 'Reference not found')}
        </p>
      )}
    </div>
  )
}

/**
 * Entity 引用预览卡片
 */
function EntityPreview({ citation }: CitationHoverCardProps) {
  const { t } = useTranslation()
  const data = citation.sourceData

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-violet-500">
        <Network className="h-4 w-4" />
        <span className="text-xs font-medium">{t('citation.entity', 'Entity')}</span>
      </div>

      <div className="space-y-1">
        <h4 className="font-medium text-sm">{data?.name || citation.refId}</h4>
        {data?.entityType && (
          <span className="inline-block px-1.5 py-0.5 text-[10px] rounded bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300">
            {data.entityType}
          </span>
        )}
      </div>

      {data?.description && (
        <p className="text-xs text-muted-foreground line-clamp-3">
          {data.description}
        </p>
      )}

      {!data && (
        <p className="text-xs text-muted-foreground italic">
          {citation.text || t('citation.notFound', 'Reference not found')}
        </p>
      )}
    </div>
  )
}

/**
 * Relationship 引用预览卡片
 */
function RelationshipPreview({ citation }: CitationHoverCardProps) {
  const { t } = useTranslation()
  const data = citation.sourceData

  // 从 refId 解析 source 和 target
  const [source, target] = citation.refId.split('->').map(s => s.trim())

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-amber-500">
        <ArrowRight className="h-4 w-4" />
        <span className="text-xs font-medium">{t('citation.relationship', 'Relationship')}</span>
      </div>

      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium">{data?.source || source}</span>
        <ArrowRight className="h-3 w-3 text-muted-foreground" />
        <span className="font-medium">{data?.target || target}</span>
      </div>

      {data?.description && (
        <p className="text-xs text-muted-foreground line-clamp-3">
          {data.description}
        </p>
      )}

      {data?.keywords && (
        <p className="text-[10px] text-muted-foreground/70">
          {t('citation.keywords', 'Keywords')}: {data.keywords}
        </p>
      )}

      {!data && citation.text && (
        <p className="text-xs text-muted-foreground italic">
          {citation.text}
        </p>
      )}
    </div>
  )
}

/**
 * Attachment 引用预览卡片
 */
function AttachmentPreview({ citation }: CitationHoverCardProps) {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const data = citation.sourceData

  const handleClick = () => {
    if (data?.entryId) {
      navigate(`/entries/${data.entryId}#attachments`)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-cyan-600 dark:text-cyan-400">
        <Paperclip className="h-4 w-4" />
        <span className="text-xs font-medium">{t('citation.attachment', 'Attachment')}</span>
      </div>

      {(data?.filename || citation.text) && (
        <h4 className="font-medium text-sm line-clamp-2">{data?.filename || citation.text}</h4>
      )}

      {data?.entryId && (
        <button
          onClick={handleClick}
          className={cn(
            "flex items-center gap-1 text-xs text-cyan-600 dark:text-cyan-400 hover:underline",
            "mt-2 pt-2 border-t border-border/50"
          )}
        >
          {t('citation.viewAttachment', 'View Attachment')}
          <ArrowRight className="h-3 w-3" />
        </button>
      )}

      {!data && (
        <p className="text-xs text-muted-foreground italic">
          {t('citation.notFound', 'Reference not found')}
        </p>
      )}
    </div>
  )
}

/**
 * 引用预览卡片 - 根据类型渲染不同内容
 */
export function CitationPreview({ citation }: CitationHoverCardProps) {
  switch (citation.type) {
    case 'entry':
      return <EntryPreview citation={citation} />
    case 'attachment':
      return <AttachmentPreview citation={citation} />
    case 'entity':
      return <EntityPreview citation={citation} />
    case 'rel':
      return <RelationshipPreview citation={citation} />
    default:
      return null
  }
}
