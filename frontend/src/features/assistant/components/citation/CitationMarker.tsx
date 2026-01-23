import { useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
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
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (!citation) return

    // Entry 类型：跳转到详情页
    if (citation.type === 'entry' && citation.sourceData?.entryId) {
      navigate(`/entries/${citation.sourceData.entryId}`)
    }
    // Entity 和 Relationship 类型：不跳转，仅显示 hover 卡片
  }

  // 根据引用类型设置样式
  const getTypeStyles = () => {
    if (!citation) return 'text-muted-foreground'

    switch (citation.type) {
      case 'entry':
        return 'text-primary hover:bg-primary/10'
      case 'entity':
        return 'text-violet-500 hover:bg-violet-500/10'
      case 'rel':
        return 'text-amber-500 hover:bg-amber-500/10'
      default:
        return 'text-muted-foreground'
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
        <sup
          onClick={handleClick}
          className={cn(
            "inline-flex items-center justify-center",
            "text-xs font-medium cursor-pointer",
            "px-1 py-0.5 rounded transition-colors",
            getTypeStyles(),
            citation.type === 'entry' && "cursor-pointer"
          )}
        >
          [{label || identifier}]
        </sup>
      </HoverCardTrigger>
      <HoverCardContent className="w-72">
        <CitationPreview citation={citation} />
      </HoverCardContent>
    </HoverCard>
  )
}
