import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Wrench,
  Network,
  PanelLeftClose,
  Plus,
} from 'lucide-react'
import type { NodeType } from '../../api/workflow'
import type { CallableWorkflowDefinition, WorkflowToolDefinition } from './types'
import { NODE_CATALOG_CATEGORIES, NODE_CATALOG_ITEMS } from './nodeCatalog'

interface NodePaletteProps {
  tools: WorkflowToolDefinition[]
  workflows: CallableWorkflowDefinition[]
}

export function NodePalette({ tools, workflows }: NodePaletteProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<'nodes' | 'tools' | 'workflows'>('nodes')
  const [keyword, setKeyword] = useState('')
  const [isCollapsed, setIsCollapsed] = useState(false)

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
        label: tool.displayName ?? tool.name,
      }),
    )
    e.dataTransfer.effectAllowed = 'move'
  }

  const onWorkflowDragStart = (e: React.DragEvent, workflow: CallableWorkflowDefinition) => {
    e.dataTransfer.setData(
      'application/workflow-call-item',
      JSON.stringify({
        nodeType: 'workflow_call',
        workflowId: workflow.id,
        label: workflow.name,
      }),
    )
    e.dataTransfer.effectAllowed = 'move'
  }

  const grouped = NODE_CATALOG_CATEGORIES.map((cat) => ({
    category: cat,
    items: NODE_CATALOG_ITEMS.filter((p) => p.category === cat),
  }))

  const visibleTools = useMemo(() => {
    const normalized = keyword.trim().toLowerCase()
    if (!normalized) return tools
    return tools.filter((tool) => {
      return (
        tool.name.toLowerCase().includes(normalized) ||
        (tool.displayName ?? '').toLowerCase().includes(normalized) ||
        (tool.description ?? '').toLowerCase().includes(normalized)
      )
    })
  }, [keyword, tools])

  const visibleWorkflows = useMemo(() => {
    const normalized = keyword.trim().toLowerCase()
    if (!normalized) return workflows
    return workflows.filter((workflow) => {
      return (
        workflow.name.toLowerCase().includes(normalized) ||
        (workflow.description ?? '').toLowerCase().includes(normalized)
      )
    })
  }, [keyword, workflows])


  return (
    <div className={`
      relative bg-white/80 backdrop-blur-md border shadow-sm rounded-xl overflow-hidden flex flex-col transition-all duration-300 ease-in-out
      ${isCollapsed ? 'w-10 h-10' : 'w-48 max-h-full'}
    `}>
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className={`
          absolute z-20 transition-all duration-300
          ${isCollapsed
            ? 'top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 p-2 rounded-full bg-primary text-primary-foreground shadow-md hover:bg-primary/90'
            : 'top-2 right-2 p-1 rounded-full hover:bg-slate-100 text-muted-foreground'
          }
        `}
        title={isCollapsed ? t('settings.skills.workflowPaletteExpand') : t('settings.skills.workflowPaletteCollapse')}
      >
        {isCollapsed ? <Plus className="w-5 h-5" /> : <PanelLeftClose className="w-3.5 h-3.5" />}
      </button>

      {
        !isCollapsed && (
          <div className="flex-1 overflow-y-auto hidden-scrollbar flex flex-col p-3">
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
              <button
                type="button"
                onClick={() => setActiveTab('workflows')}
                className={`flex-1 rounded-md px-2 py-1 text-[10px] font-medium transition-all ${activeTab === 'workflows' ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground/80'
                  }`}
              >
                {t('settings.skills.workflowPaletteWorkflows')}
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
                      <span className="text-[11px] font-medium text-muted-foreground group-hover:text-foreground transition-colors truncate">
                        {tool.displayName ?? tool.name}
                      </span>
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

            {activeTab === 'workflows' && (
              <div className="space-y-2">
                <input
                  type="text"
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder={t('settings.skills.workflowWorkflowSearchPlaceholder')}
                  className="w-full px-2 py-1.5 text-[10px] rounded-md border bg-white/50 focus:bg-white transition-colors"
                />
                <div className="space-y-1">
                  {visibleWorkflows.map((workflow) => (
                    <div
                      key={workflow.id}
                      draggable
                      onDragStart={(e) => onWorkflowDragStart(e, workflow)}
                      className="group flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-transparent hover:border-border/50 bg-transparent hover:bg-white/50 cursor-grab active:cursor-grabbing transition-all text-sm"
                      title={workflow.description ?? undefined}
                    >
                      <div className="p-1 rounded-md bg-white shadow-sm ring-1 ring-black/5 group-hover:scale-105 transition-transform">
                        <Network className="w-3.5 h-3.5 opacity-70 group-hover:opacity-100 text-emerald-500" />
                      </div>
                      <span className="text-[11px] font-medium text-muted-foreground group-hover:text-foreground transition-colors truncate">
                        {workflow.name}
                      </span>
                    </div>
                  ))}
                </div>
                {visibleWorkflows.length === 0 && (
                  <p className="text-[10px] text-muted-foreground px-1 py-1 text-center opacity-60">
                    {t('settings.skills.workflowNoCallableWorkflows')}
                  </p>
                )}
              </div>
            )}
          </div>
        )
      }
    </div>
  )
}
