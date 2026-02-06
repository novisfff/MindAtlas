import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { FileText, Paperclip, ChevronRight, ChevronDown, Layers, Box, Share2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCitationContextSafe } from './CitationContext'
import { CitationData } from './utils'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

/**
 * ReferenceList - 在消息末尾展示所有检索到的引用
 */
interface ReferenceListProps {
  content?: string
  isStreaming?: boolean
}

export function ReferenceList({ content, isStreaming }: ReferenceListProps) {
  const { t } = useTranslation()
  const context = useCitationContextSafe()
  const navigate = useNavigate()
  const [isKgExpanded, setIsKgExpanded] = useState(false)

  // 解析 content 中实际使用的引用标记 [^n]，使用 useMemo 缓存
  const usedIndices = useMemo(() => {
    const indices = new Set<string>()
    if (content) {
      const citationRegex = /\[\^(\d+)\]/g
      let match: RegExpExecArray | null
      while ((match = citationRegex.exec(content)) !== null) {
        indices.add(match[1])
      }
    }
    return indices
  }, [content])

  // 流式输出时隐藏参考来源
  if (isStreaming) return null

  if (!context || context.registry.size === 0) {
    return null
  }

  // 按类型分组，只显示在原文中被实际引用的来源
  const entries: CitationData[] = []
  const attachments: CitationData[] = []
  const kgItems: CitationData[] = []

  context.registry.forEach((citation) => {
    // 过滤未在原文中被引用的来源
    if (!usedIndices.has(citation.index)) {
      return
    }

    switch (citation.type) {
      case 'entry':
        entries.push(citation)
        break
      case 'attachment':
        attachments.push(citation)
        break
      case 'entity':
      case 'rel':
        kgItems.push(citation)
        break
    }
  })

  // 如果没有引用，直接返回null
  if (entries.length === 0 && attachments.length === 0 && kgItems.length === 0) {
    return null
  }

  const handleEntryClick = (entryId?: string) => {
    if (entryId) {
      navigate(`/entries/${entryId}`)
    }
  }

  const handleAttachmentClick = (entryId?: string) => {
    if (entryId) {
      navigate(`/entries/${entryId}#attachments`)
    }
  }

  const renderKgSection = () => {
    if (kgItems.length === 0) return null

    return (
      <div className="mt-2">
        <button
          onClick={() => setIsKgExpanded(!isKgExpanded)}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors w-full text-left py-1"
        >
          {isKgExpanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          <Layers className="h-3.5 w-3.5" />
          <span className="font-medium">
            {t('citation.knowledgeGraph', 'Knowledge Graph References')} ({kgItems.length})
          </span>
        </button>

        {isKgExpanded && (
          <div className="space-y-1.5 mt-1.5 pl-2 border-l border-border/50 ml-1.5">
            {kgItems.map((citation) => (
              <ReferenceItem
                key={citation.index}
                citation={citation}
                icon={
                  citation.type === 'entity' ? (
                    <Box className="h-3.5 w-3.5" />
                  ) : (
                    <Share2 className="h-3.5 w-3.5" />
                  )
                }
                iconColor={
                  citation.type === 'entity' ? "text-violet-500" : "text-amber-500"
                }
              />
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="mt-4 pt-3 border-t border-border/50">
      <div className="text-xs font-medium text-muted-foreground mb-2">
        {t('citation.references', 'References')}
      </div>
      <div className="space-y-1.5">
        {/* Entry 引用 - 总是展开 */}
        {entries.map((citation) => (
          <ReferenceItem
            key={citation.index}
            citation={citation}
            icon={<FileText className="h-3.5 w-3.5" />}
            iconColor="text-primary"
            onClick={() => handleEntryClick(citation.sourceData?.entryId)}
            clickable
          />
        ))}

        {/* Attachment 引用 - 总是展开 */}
        {attachments.map((citation) => (
          <ReferenceItem
            key={citation.index}
            citation={citation}
            icon={<Paperclip className="h-3.5 w-3.5" />}
            iconColor="text-cyan-600 dark:text-cyan-400"
            onClick={() => handleAttachmentClick(citation.sourceData?.entryId)}
            clickable
          />
        ))}

        {/* 知识图谱引用 - 可折叠 */}
        {renderKgSection()}
      </div>
    </div>
  )
}

interface ReferenceItemProps {
  citation: CitationData
  icon: React.ReactNode
  iconColor: string
  onClick?: () => void
  clickable?: boolean
}

function ReferenceItem({ citation, icon, iconColor, onClick, clickable }: ReferenceItemProps) {
  const { t } = useTranslation()
  const data = citation.sourceData

  const getTitle = () => {
    switch (citation.type) {
      case 'entry':
        return data?.title || citation.text
      case 'attachment':
        return data?.filename || citation.text || citation.refId
      case 'entity':
        return data?.name || citation.refId
      case 'rel':
        return `${data?.source || ''} → ${data?.target || ''}`
      default:
        return citation.text
    }
  }

  const getDescription = () => {
    switch (citation.type) {
      case 'entry':
        return data?.summary
      case 'attachment':
        return null
      case 'entity':
        return data?.description
      case 'rel':
        return data?.description || data?.keywords
      default:
        return null
    }
  }

  const getTypeLabel = () => {
    switch (citation.type) {
      case 'entry':
        return t('citation.entry', 'Entry')
      case 'attachment':
        return t('citation.attachment', 'Attachment')
      case 'entity':
        return data?.entityType || t('citation.entity', 'Entity')
      case 'rel':
        return t('citation.relationship', 'Relationship')
      default:
        return ''
    }
  }

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <div
          onClick={clickable ? onClick : undefined}
          className={cn(
            "flex items-center gap-2 p-1.5 rounded-md bg-background/50",
            "border border-border/30 text-xs w-full max-w-fit transition-colors",
            clickable && "cursor-pointer hover:bg-accent/50 hover:border-border/50"
          )}
        >
          <span className={cn("shrink-0", iconColor)}>{icon}</span>
          <span className={cn(
            "px-1 rounded text-[10px] font-medium shrink-0",
            citation.type === 'entry' && "bg-primary/10 text-primary",
            citation.type === 'attachment' && "bg-cyan-600/10 text-cyan-600 dark:bg-cyan-400/10 dark:text-cyan-400",
            citation.type === 'entity' && "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
            citation.type === 'rel' && "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300"
          )}>
            [{citation.index}]
          </span>
          <span className="font-medium truncate max-w-[300px]">{getTitle()}</span>
        </div>
      </HoverCardTrigger>

      <HoverCardContent align="start" className="w-[320px] p-3">
        <div className="space-y-2">
          <div className="flex items-start gap-2">
            <span className={cn("mt-0.5 shrink-0", iconColor)}>{icon}</span>
            <div className="space-y-0.5">
              <h4 className="text-sm font-semibold">{getTitle()}</h4>
              <div className="flex items-center gap-1.5">
                <span className={cn(
                  "inline-block px-1 py-0.5 rounded text-[10px] font-medium",
                  citation.type === 'entry' && "bg-primary/10 text-primary",
                  citation.type === 'attachment' && "bg-cyan-600/10 text-cyan-600 dark:bg-cyan-400/10 dark:text-cyan-400",
                  citation.type === 'entity' && "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
                  citation.type === 'rel' && "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300"
                )}>
                  {getTypeLabel()}
                </span>
              </div>
            </div>
          </div>

          {getDescription() && (
            <div className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap max-h-[200px] overflow-y-auto custom-scrollbar">
              {getDescription()}
            </div>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
