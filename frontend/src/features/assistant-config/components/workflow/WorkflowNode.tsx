import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
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
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { useWorkflowEditorStore } from '../../stores/workflow-editor-store'
import type { NodeType } from '../../api/workflow'
import { normalizeIfElseConfig } from './ifElseConfig'

const NODE_STYLES: Record<NodeType, { header: string; icon: typeof Play; iconColor: string }> = {
  start: { header: 'bg-green-50 border-b border-green-100', icon: Play, iconColor: 'text-green-600' },
  llm: { header: 'bg-purple-50 border-b border-purple-100', icon: Brain, iconColor: 'text-purple-600' },
  tool: { header: 'bg-sky-50 border-b border-sky-100', icon: Wrench, iconColor: 'text-sky-600' },
  if_else: { header: 'bg-yellow-50 border-b border-yellow-100', icon: GitBranch, iconColor: 'text-yellow-600' },
  template: { header: 'bg-cyan-50 border-b border-cyan-100', icon: FileText, iconColor: 'text-cyan-600' },
  parameter_extractor: { header: 'bg-pink-50 border-b border-pink-100', icon: ScanSearch, iconColor: 'text-pink-600' },
  knowledge_retrieval: { header: 'bg-teal-50 border-b border-teal-100', icon: BookOpen, iconColor: 'text-teal-600' },
  variable_aggregator: { header: 'bg-indigo-50 border-b border-indigo-100', icon: Merge, iconColor: 'text-indigo-600' },
}
const HANDLE_TOP_OFFSET = 28

function getPreview(data: WfNodeData): string {
  const cfg = data.config as Record<string, unknown> | null
  if (!cfg) return ''
  switch (data.nodeType) {
    case 'llm':
      return (cfg.isOutput ? '[Output] ' : '') + truncate(cfg.systemPrompt as string, 50)
    case 'tool':
      return (cfg.toolName as string) || ''
    case 'template':
      return truncate(cfg.template as string, 50)
    case 'if_else': {
      const normalized = normalizeIfElseConfig(cfg)
      const elifCount = Math.max(0, normalized.branches.length - 1)
      return `IF${elifCount > 0 ? ` + ${elifCount} ELIF` : ''} + ELSE`
    }
    case 'parameter_extractor':
      return truncate(cfg.instruction as string, 50)
    case 'knowledge_retrieval':
      return truncate(cfg.query as string, 50)
    default:
      return ''
  }
}

function truncate(s: string | undefined | null, max: number): string {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

function WorkflowNodeInner({ id, data }: NodeProps) {
  const nodeData = data as unknown as WfNodeData
  const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)
  const isSelected = selectedNodeId === id
  const style = NODE_STYLES[nodeData.nodeType] ?? NODE_STYLES.llm
  const Icon = style.icon
  const preview = getPreview(nodeData)
  const previewText = preview || '\u00A0'
  const isStart = nodeData.nodeType === 'start'
  const isIfElse = nodeData.nodeType === 'if_else'

  return (
    <div
      className={`
        relative w-[240px] rounded-xl bg-white shadow-sm border transaction-all duration-200
        ${isSelected ? 'ring-2 ring-primary border-primary shadow-md' : 'border-border hover:shadow-md'}
      `}
    >
      {/* Header */}
      <div className={`flex items-center gap-2 px-3 py-2 rounded-t-xl ${style.header}`}>
        <div className={`p-1 rounded-md bg-white/80 ${style.iconColor}`}>
          <Icon className="w-3.5 h-3.5" />
        </div>
        <span className="text-xs font-semibold text-foreground/80 truncate flex-1">
          {nodeData.label || nodeData.nodeType}
        </span>
      </div>

      {/* Body */}
      <div className="px-3 py-3 min-h-[50px]">
        <p className={`text-[11px] leading-relaxed text-muted-foreground line-clamp-3 ${preview ? '' : 'italic opacity-50'}`}>
          {previewText || 'No configuration'}
        </p>
      </div>

      {/* Input handle */}
      {!isStart && (
        <Handle
          type="target"
          position={Position.Left}
          id="input"
          style={{ top: `${HANDLE_TOP_OFFSET}px` }}
          className="!w-2.5 !h-2.5 !bg-white !border-2 !border-muted-foreground/50 hover:!border-primary transition-colors"
        />
      )}

      {/* Output handle(s) */}
      {!isIfElse && (
        <Handle
          type="source"
          position={Position.Right}
          id="output"
          style={{ top: `${HANDLE_TOP_OFFSET}px` }}
          className="!w-2.5 !h-2.5 !bg-white !border-2 !border-muted-foreground/50 hover:!border-primary transition-colors"
        />
      )}

      {/* IF/ELSE: dynamic output handles */}
      {isIfElse && (
        <div className="absolute -right-[5px] top-[50px] bottom-3 flex flex-col justify-between py-1">
          {(() => {
            const cfg = nodeData.config as Record<string, unknown> | null
            const normalized = normalizeIfElseConfig(cfg)
            const handles = normalized.branches.map((branch) => branch.id)
            handles.push(normalized.elseHandle || 'else')

            return handles.map((handle, i) => (
              <div key={handle} className="relative group flex items-center h-4">
                {/* Tooltip-ish label for the branch could go here */}
                <Handle
                  type="source"
                  position={Position.Right}
                  id={handle}
                  style={{ position: 'static', transform: 'none' }}
                  className={`!w-2.5 !h-2.5 !bg-white !border-2 ${handle === (normalized.elseHandle || 'else') ? '!border-stone-400' : '!border-green-500'} hover:!scale-125 transition-all`}
                />
                <span className="absolute left-4 text-[9px] font-mono text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity bg-white px-1 border rounded shadow-sm whitespace-nowrap z-50 pointer-events-none">
                  {handle}
                </span>
              </div>
            ))
          })()}
        </div>
      )}
    </div>
  )
}

export const WorkflowNode = memo(WorkflowNodeInner)
