import { Brain, type LucideIcon, GitBranch, Infinity, RefreshCw, ScanSearch, BookOpen, SendHorizontal } from 'lucide-react'
import type { NodeType } from '../../api/workflow'

export type NodeCatalogCategory = 'basic' | 'logic' | 'data' | 'output'

export interface NodeCatalogItem {
  type: Exclude<NodeType, 'start' | 'tool'>
  icon: LucideIcon
  category: NodeCatalogCategory
}

export const NODE_CATALOG_ITEMS: NodeCatalogItem[] = [
  { type: 'llm', icon: Brain, category: 'basic' },
  { type: 'if_else', icon: GitBranch, category: 'logic' },
  { type: 'iteration', icon: RefreshCw, category: 'logic' },
  { type: 'loop', icon: Infinity, category: 'logic' },
  { type: 'parameter_extractor', icon: ScanSearch, category: 'data' },
  { type: 'knowledge_retrieval', icon: BookOpen, category: 'data' },
  { type: 'output', icon: SendHorizontal, category: 'output' },
]

export const NODE_CATALOG_CATEGORIES: NodeCatalogCategory[] = ['basic', 'logic', 'data', 'output']
