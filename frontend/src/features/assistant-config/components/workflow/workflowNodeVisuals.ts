import type { ContainerBodyNodeType, NodeType } from '../../api/workflow'

export type WorkflowVisualNodeType = NodeType | ContainerBodyNodeType | string

export type WorkflowNodeTone = {
  editorHeaderClass: string
  editorIconColorClass: string
  thumbnailSurfaceClass: string
  thumbnailBorderClass: string
  thumbnailAccentClass: string
  thumbnailDenseClass: string
}

const WORKFLOW_NODE_TONES: Record<string, WorkflowNodeTone> = {
  start: {
    editorHeaderClass: 'bg-gradient-to-r from-emerald-100/90 to-green-100/90 border-b border-emerald-200',
    editorIconColorClass: 'text-emerald-700',
    thumbnailSurfaceClass: 'bg-emerald-50/45',
    thumbnailBorderClass: 'border-emerald-300/65',
    thumbnailAccentClass: 'bg-emerald-500/72',
    thumbnailDenseClass: 'border-emerald-400/80',
  },
  llm: {
    editorHeaderClass: 'bg-gradient-to-r from-violet-100/90 to-purple-100/90 border-b border-violet-200',
    editorIconColorClass: 'text-violet-700',
    thumbnailSurfaceClass: 'bg-violet-50/45',
    thumbnailBorderClass: 'border-violet-300/65',
    thumbnailAccentClass: 'bg-violet-500/70',
    thumbnailDenseClass: 'border-violet-400/80',
  },
  agent: {
    editorHeaderClass: 'bg-gradient-to-r from-indigo-100/90 to-sky-100/90 border-b border-indigo-200',
    editorIconColorClass: 'text-indigo-700',
    thumbnailSurfaceClass: 'bg-indigo-50/45',
    thumbnailBorderClass: 'border-indigo-300/65',
    thumbnailAccentClass: 'bg-indigo-500/70',
    thumbnailDenseClass: 'border-indigo-400/80',
  },
  tool: {
    editorHeaderClass: 'bg-gradient-to-r from-sky-100/90 to-blue-100/90 border-b border-sky-200',
    editorIconColorClass: 'text-sky-700',
    thumbnailSurfaceClass: 'bg-sky-50/45',
    thumbnailBorderClass: 'border-sky-300/65',
    thumbnailAccentClass: 'bg-sky-500/72',
    thumbnailDenseClass: 'border-sky-400/80',
  },
  workflow_call: {
    editorHeaderClass: 'bg-gradient-to-r from-emerald-100/90 to-teal-100/90 border-b border-emerald-200',
    editorIconColorClass: 'text-emerald-700',
    thumbnailSurfaceClass: 'bg-teal-50/45',
    thumbnailBorderClass: 'border-teal-300/65',
    thumbnailAccentClass: 'bg-teal-500/72',
    thumbnailDenseClass: 'border-teal-400/80',
  },
  if_else: {
    editorHeaderClass: 'bg-gradient-to-r from-amber-100/90 to-yellow-100/90 border-b border-amber-200',
    editorIconColorClass: 'text-amber-700',
    thumbnailSurfaceClass: 'bg-amber-50/45',
    thumbnailBorderClass: 'border-amber-300/68',
    thumbnailAccentClass: 'bg-amber-500/72',
    thumbnailDenseClass: 'border-amber-400/80',
  },
  parameter_extractor: {
    editorHeaderClass: 'bg-gradient-to-r from-fuchsia-100/90 to-pink-100/90 border-b border-fuchsia-200',
    editorIconColorClass: 'text-fuchsia-700',
    thumbnailSurfaceClass: 'bg-fuchsia-50/45',
    thumbnailBorderClass: 'border-fuchsia-300/65',
    thumbnailAccentClass: 'bg-fuchsia-500/70',
    thumbnailDenseClass: 'border-fuchsia-400/80',
  },
  knowledge_retrieval: {
    editorHeaderClass: 'bg-gradient-to-r from-teal-100/90 to-emerald-100/90 border-b border-teal-200',
    editorIconColorClass: 'text-teal-700',
    thumbnailSurfaceClass: 'bg-teal-50/45',
    thumbnailBorderClass: 'border-teal-300/65',
    thumbnailAccentClass: 'bg-teal-500/72',
    thumbnailDenseClass: 'border-teal-400/80',
  },
  iteration: {
    editorHeaderClass: 'bg-gradient-to-r from-cyan-100/90 to-sky-100/90 border-b border-cyan-200',
    editorIconColorClass: 'text-cyan-700',
    thumbnailSurfaceClass: 'bg-cyan-50/45',
    thumbnailBorderClass: 'border-cyan-300/65',
    thumbnailAccentClass: 'bg-cyan-500/72',
    thumbnailDenseClass: 'border-cyan-400/80',
  },
  loop: {
    editorHeaderClass: 'bg-gradient-to-r from-indigo-100/90 to-blue-100/90 border-b border-indigo-200',
    editorIconColorClass: 'text-indigo-700',
    thumbnailSurfaceClass: 'bg-indigo-50/45',
    thumbnailBorderClass: 'border-indigo-300/65',
    thumbnailAccentClass: 'bg-indigo-500/70',
    thumbnailDenseClass: 'border-indigo-400/80',
  },
  code_executor: {
    editorHeaderClass: 'bg-gradient-to-r from-orange-100/90 to-amber-100/90 border-b border-orange-200',
    editorIconColorClass: 'text-orange-700',
    thumbnailSurfaceClass: 'bg-orange-50/45',
    thumbnailBorderClass: 'border-orange-300/68',
    thumbnailAccentClass: 'bg-orange-500/72',
    thumbnailDenseClass: 'border-orange-400/80',
  },
  http_request: {
    editorHeaderClass: 'bg-gradient-to-r from-blue-100/90 to-indigo-100/90 border-b border-blue-200',
    editorIconColorClass: 'text-blue-700',
    thumbnailSurfaceClass: 'bg-blue-50/45',
    thumbnailBorderClass: 'border-blue-300/65',
    thumbnailAccentClass: 'bg-blue-500/70',
    thumbnailDenseClass: 'border-blue-400/80',
  },
  variable_assign: {
    editorHeaderClass: 'bg-gradient-to-r from-lime-100/90 to-emerald-100/90 border-b border-lime-200',
    editorIconColorClass: 'text-lime-700',
    thumbnailSurfaceClass: 'bg-lime-50/45',
    thumbnailBorderClass: 'border-lime-300/65',
    thumbnailAccentClass: 'bg-lime-500/72',
    thumbnailDenseClass: 'border-lime-400/80',
  },
  human_in_loop: {
    editorHeaderClass: 'bg-gradient-to-r from-blue-100/90 to-cyan-100/90 border-b border-blue-200',
    editorIconColorClass: 'text-blue-700',
    thumbnailSurfaceClass: 'bg-blue-50/45',
    thumbnailBorderClass: 'border-blue-300/65',
    thumbnailAccentClass: 'bg-blue-500/70',
    thumbnailDenseClass: 'border-blue-400/80',
  },
  output: {
    editorHeaderClass: 'bg-gradient-to-r from-rose-100/90 to-orange-100/90 border-b border-rose-200',
    editorIconColorClass: 'text-rose-700',
    thumbnailSurfaceClass: 'bg-rose-50/45',
    thumbnailBorderClass: 'border-rose-300/65',
    thumbnailAccentClass: 'bg-rose-500/72',
    thumbnailDenseClass: 'border-rose-400/80',
  },
}

export function resolveWorkflowNodeTone(nodeType: WorkflowVisualNodeType): WorkflowNodeTone {
  const normalized = String(nodeType ?? '').trim()
  return WORKFLOW_NODE_TONES[normalized] ?? WORKFLOW_NODE_TONES.llm
}
