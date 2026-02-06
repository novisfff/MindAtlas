import { useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { FileText, Box, Share2, Paperclip } from 'lucide-react'
import { HoverCard, HoverCardTrigger, HoverCardContent } from '@/components/ui/hover-card'
import { useCitationContextSafe } from './CitationContext'
import { CitationPreview } from './CitationHoverCard'

interface CitationMarkerProps {
  identifier: string
  label?: string
}

/**
 * CitationMarker - 上角标引用标记组件
 * 用于替换 ReactMarkdown 的 footnoteReference
 */
export function CitationMarker({ identifier, label }: CitationMarkerProps) {
  const navigate = useNavigate()
  const context = useCitationContextSafe()

  // 获取引用数据
  const citation = context?.getCitation(identifier)

  // 处理点击事件
  const handleClick = (e: React.MouseEvent | React.KeyboardEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (!citation) return

    // Entry 类型：跳转到详情页
    if (citation.type === 'entry' && citation.sourceData?.entryId) {
      navigate(`/entries/${citation.sourceData.entryId}`)
    }
    // Attachment 类型：跳转到关联 Entry 的附件区
    if (citation.type === 'attachment' && citation.sourceData?.entryId) {
      navigate(`/entries/${citation.sourceData.entryId}#attachments`)
    }
    // Entity 和 Relationship 类型：不跳转，仅显示 hover 卡片
  }

  // 处理键盘事件
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      handleClick(e)
    }
  }

  // 根据引用类型设置样式
  const getTypeStyles = () => {
    if (!citation) return 'text-muted-foreground'

    switch (citation.type) {
      case 'entry':
        return 'text-primary bg-primary/10 hover:bg-primary/20'
      case 'attachment':
        return 'text-cyan-600 bg-cyan-600/10 hover:bg-cyan-600/20 dark:text-cyan-400 dark:bg-cyan-400/10 dark:hover:bg-cyan-400/20'
      case 'entity':
        return 'text-violet-500 bg-violet-500/10 hover:bg-violet-500/20'
      case 'rel':
        return 'text-amber-500 bg-amber-500/10 hover:bg-amber-500/20'
      default:
        return 'text-muted-foreground bg-muted/50 hover:bg-muted/70'
    }
  }

  // 无引用数据时的降级渲染
  if (!citation) {
    return (
      <sup className="text-xs text-muted-foreground">
        [{label || identifier}]
      </sup>
    )
  }

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          onClick={handleClick}
          onKeyDown={handleKeyDown}
          className={cn(
            "inline-flex items-center justify-center",
            "cursor-pointer align-super",
            "p-0.5 rounded transition-colors",
            "focus:outline-none focus:ring-2 focus:ring-primary/50",
            getTypeStyles()
          )}
          aria-label={`Citation ${label || identifier}`}
        >
          {citation.type === 'entry' && <FileText className="h-3 w-3" />}
          {citation.type === 'attachment' && <Paperclip className="h-3 w-3" />}
          {citation.type === 'entity' && <Box className="h-3 w-3" />}
          {citation.type === 'rel' && <Share2 className="h-3 w-3" />}
          <span className="ml-0.5 text-xs font-medium leading-none">[{label || identifier}]</span>
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="w-72">
        <CitationPreview citation={citation} />
      </HoverCardContent>
    </HoverCard>
  )
}
