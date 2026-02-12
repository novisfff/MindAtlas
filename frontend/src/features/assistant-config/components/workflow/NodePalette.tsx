import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Play,
  Brain,
  Wrench,
  GitBranch,
  FileText,
  ScanSearch,
  BookOpen,
  Merge,
} from 'lucide-react'
import type { NodeType } from '../../api/workflow'
import type { WorkflowToolDefinition } from './types'

interface PaletteItem {
  type: NodeType
  icon: typeof Play
  category: 'basic' | 'logic' | 'data'
}

const PALETTE_ITEMS: PaletteItem[] = [
  { type: 'llm', icon: Brain, category: 'basic' },
  { type: 'template', icon: FileText, category: 'basic' },
  { type: 'if_else', icon: GitBranch, category: 'logic' },
  { type: 'variable_aggregator', icon: Merge, category: 'logic' },
  { type: 'parameter_extractor', icon: ScanSearch, category: 'data' },
  { type: 'knowledge_retrieval', icon: BookOpen, category: 'data' },
]

const CATEGORIES = ['basic', 'logic', 'data'] as const

interface NodePaletteProps {
  tools: WorkflowToolDefinition[]
}

export function NodePalette({ tools }: NodePaletteProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<'nodes' | 'tools'>('nodes')
  const [keyword, setKeyword] = useState('')

  const onDragStart = (e: React.DragEvent, nodeType: NodeType) => {
    e.dataTransfer.setData('application/workflow-node-type', nodeType)
    e.dataTransfer.effectAllowed = 'move'
  }

  const onToolDragStart = (e: React.DragEvent, tool: WorkflowToolDefinition) => {
    e.dataTransfer.setData(
      'application/workflow-tool-item',
      JSON.stringify({
        nodeType: 'tool',
        toolName: tool.name,
        label: tool.name,
      }),
    )
    e.dataTransfer.effectAllowed = 'move'
  }

  const grouped = CATEGORIES.map((cat) => ({
    category: cat,
    items: PALETTE_ITEMS.filter((p) => p.category === cat),
  }))

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

  return (
    <div className="w-48 bg-white/80 backdrop-blur-md border shadow-sm rounded-xl overflow-y-auto hidden-scrollbar flex flex-col">
      <div className="p-3">
        <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 px-1">
          {t('settings.skills.nodePalette')}
        </h2>
        <div className="mb-2 flex rounded-lg border bg-muted/50 p-0.5">
          <button
            type="button"
            onClick={() => setActiveTab('nodes')}
            className={`flex-1 rounded-md px-2 py-1 text-[10px] font-medium transition-all ${activeTab === 'nodes' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground/80'
              }`}
          >
            {t('settings.skills.workflowPaletteNodes')}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('tools')}
            className={`flex-1 rounded-md px-2 py-1 text-[10px] font-medium transition-all ${activeTab === 'tools' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground/80'
              }`}
          >
            {t('settings.skills.workflowPaletteTools')}
          </button>
        </div>

        {activeTab === 'nodes' && (
          <>
            {grouped.map(({ category, items }) => (
              <div key={category} className="mb-3">
                <p className="text-[10px] font-medium text-muted-foreground/60 uppercase mb-1.5 px-1">
                  {t(`settings.skills.nodeCategories.${category}`)}
                </p>
                <div className="space-y-1">
                  {items.map(({ type, icon: Icon }) => (
                    <div
                      key={type}
                      draggable
                      onDragStart={(e) => onDragStart(e, type)}
                      className="group flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-transparent hover:border-border/50 bg-transparent hover:bg-white/50 cursor-grab active:cursor-grabbing transition-all text-sm"
                    >
                      <div className="p-1 rounded-md bg-white shadow-sm ring-1 ring-black/5 group-hover:scale-105 transition-transform">
                        <Icon className="w-3.5 h-3.5 opacity-70 group-hover:opacity-100 text-primary" />
                      </div>
                      <span className="text-[11px] font-medium text-muted-foreground group-hover:text-foreground transition-colors truncate">
                        {t(`settings.skills.nodeTypes.${type}`)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </>
        )}

        {activeTab === 'tools' && (
          <div className="space-y-2">
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={t('settings.skills.workflowToolSearchPlaceholder')}
              className="w-full px-2 py-1.5 text-[10px] rounded-md border bg-white/50 focus:bg-white transition-colors"
            />
            <div className="space-y-1">
              {visibleTools.map((tool) => (
                <div
                  key={tool.name}
                  draggable
                  onDragStart={(e) => onToolDragStart(e, tool)}
                  className="group flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-transparent hover:border-border/50 bg-transparent hover:bg-white/50 cursor-grab active:cursor-grabbing transition-all text-sm"
                  title={tool.description ?? undefined}
                >
                  <div className="p-1 rounded-md bg-white shadow-sm ring-1 ring-black/5 group-hover:scale-105 transition-transform">
                    <Wrench className="w-3.5 h-3.5 opacity-70 group-hover:opacity-100 text-sky-500" />
                  </div>
                  <span className="text-[11px] font-medium text-muted-foreground group-hover:text-foreground transition-colors truncate">{tool.name}</span>
                </div>
              ))}
            </div>
            {visibleTools.length === 0 && (
              <p className="text-[10px] text-muted-foreground px-1 py-1 text-center opacity-60">
                {t('settings.skills.workflowNoTools')}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
