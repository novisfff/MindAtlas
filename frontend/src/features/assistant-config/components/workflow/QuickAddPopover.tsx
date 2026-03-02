import { useMemo, useState, type ReactElement } from 'react'
import { Search, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { ContainerBodyNodeType, NodeType } from '../../api/workflow'
import type { WorkflowToolDefinition } from './types'
import { NODE_CATALOG_CATEGORIES, NODE_CATALOG_ITEMS } from './nodeCatalog'

export type QuickAddPayload =
  | { kind: 'node'; nodeType: NodeType | ContainerBodyNodeType }
  | { kind: 'tool'; toolName: string }

interface QuickAddPopoverProps {
  trigger?: ReactElement
  anchor?: ReactElement
  tools: WorkflowToolDefinition[]
  onSelect: (payload: QuickAddPayload) => void
  scope?: 'main' | 'container'
  open?: boolean
  onOpenChange?: (open: boolean) => void
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
  sideOffset?: number
}

const CONTAINER_ALLOWED_TYPES = new Set<ContainerBodyNodeType>([
  'llm',
  'tool',
  'if_else',
  'parameter_extractor',
  'knowledge_retrieval',
  'code_executor',
  'http_request',
  'variable_assign',
  'human_in_loop',
])

export function QuickAddPopover({
  trigger,
  anchor,
  tools,
  onSelect,
  scope = 'main',
  open,
  onOpenChange,
  side = 'right',
  align = 'start',
  sideOffset = 10,
}: QuickAddPopoverProps) {
  const { t } = useTranslation()
  const [internalOpen, setInternalOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'nodes' | 'tools'>('nodes')
  const [keyword, setKeyword] = useState('')
  const isControlled = typeof open === 'boolean'
  const currentOpen = isControlled ? Boolean(open) : internalOpen

  const handleOpenChange = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
    if (!nextOpen) {
      setKeyword('')
      setActiveTab('nodes')
    }
  }

  const visibleNodeItems = useMemo(() => {
    const normalized = keyword.trim().toLowerCase()
    return NODE_CATALOG_ITEMS.filter((item) => {
      if (scope === 'container' && !CONTAINER_ALLOWED_TYPES.has(item.type as ContainerBodyNodeType)) {
        return false
      }
      if (!normalized) return true
      const label = t(`settings.skills.nodeTypes.${item.type}`).toLowerCase()
      return label.includes(normalized)
    })
  }, [keyword, scope, t])

  const groupedNodes = useMemo(
    () =>
      NODE_CATALOG_CATEGORIES.map((category) => ({
        category,
        items: visibleNodeItems.filter((item) => item.category === category),
      })).filter((group) => group.items.length > 0),
    [visibleNodeItems],
  )

  const visibleTools = useMemo(() => {
    const normalized = keyword.trim().toLowerCase()
    if (!normalized) return tools
    return tools.filter((tool) => {
      return (
        tool.name.toLowerCase().includes(normalized) ||
        (tool.description ?? '').toLowerCase().includes(normalized)
      )
    })
  }, [keyword, tools])

  const handleChooseNode = (nodeType: NodeType | ContainerBodyNodeType) => {
    onSelect({ kind: 'node', nodeType })
    handleOpenChange(false)
  }

  const handleChooseTool = (toolName: string) => {
    onSelect({ kind: 'tool', toolName })
    handleOpenChange(false)
  }

  return (
    <Popover
      open={currentOpen}
      onOpenChange={handleOpenChange}
    >
      {anchor ? <PopoverAnchor asChild>{anchor}</PopoverAnchor> : null}
      {trigger ? <PopoverTrigger asChild>{trigger}</PopoverTrigger> : null}
      <PopoverContent
        side={side}
        align={align}
        sideOffset={sideOffset}
        className="w-[360px] p-0 border shadow-2xl bg-white rounded-2xl"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="p-3 border-b bg-muted/10 rounded-t-2xl">
          <div className="flex rounded-xl border bg-muted/40 p-1">
            <button
              type="button"
              onClick={() => setActiveTab('nodes')}
              className={`flex-1 rounded-lg px-2.5 py-1.5 text-sm font-semibold transition-colors ${activeTab === 'nodes' ? 'bg-white text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
            >
              {t('settings.skills.workflowPaletteNodes')}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('tools')}
              className={`flex-1 rounded-lg px-2.5 py-1.5 text-sm font-semibold transition-colors ${activeTab === 'tools' ? 'bg-white text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
            >
              {t('settings.skills.workflowPaletteTools')}
            </button>
          </div>
          <div className="relative mt-2.5">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/70" />
            <input
              type="text"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder={activeTab === 'nodes' ? t('settings.skills.nodePalette') : t('settings.skills.workflowToolSearchPlaceholder')}
              className="w-full h-10 rounded-lg border bg-background pl-8 pr-2 text-sm outline-none focus:border-primary/50"
            />
          </div>
        </div>

        <div className="max-h-[460px] overflow-y-auto p-2.5">
          {activeTab === 'nodes' && groupedNodes.map((group) => (
            <div key={group.category} className="mb-2.5">
              <div className="px-2 py-1 text-base font-semibold text-muted-foreground/90">
                {t(`settings.skills.nodeCategories.${group.category}`)}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const Icon = item.icon
                  return (
                    <button
                      key={item.type}
                      type="button"
                      onClick={() => handleChooseNode(item.type)}
                      className="w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-muted/50"
                    >
                      <div className="p-1.5 rounded-md bg-white ring-1 ring-black/5">
                        <Icon className="w-3.5 h-3.5 text-primary" />
                      </div>
                      <span className="text-[18px] leading-none font-medium">{t(`settings.skills.nodeTypes.${item.type}`)}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}

          {activeTab === 'tools' && (
            <div className="space-y-1">
              {visibleTools.map((tool) => (
                <button
                  key={tool.name}
                  type="button"
                  onClick={() => handleChooseTool(tool.name)}
                  className="w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-muted/50"
                  title={tool.description ?? undefined}
                >
                  <div className="p-1.5 rounded-md bg-white ring-1 ring-black/5">
                    <Wrench className="w-3.5 h-3.5 text-sky-500" />
                  </div>
                  <span className="text-[18px] leading-none font-medium truncate">{tool.name}</span>
                </button>
              ))}
              {visibleTools.length === 0 && (
                <div className="text-xs text-muted-foreground text-center py-3">
                  {t('settings.skills.workflowNoTools')}
                </div>
              )}
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
