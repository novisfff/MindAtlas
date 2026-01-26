import { createContext, useContext, useMemo, ReactNode } from 'react'
import {
  CitationData,
  extractKbSearchResults,
  generateRegistryFromKbResult,
} from './utils'

// Context 类型
interface CitationContextValue {
  registry: Map<string, CitationData>
  getCitation: (index: string) => CitationData | undefined
}

const CitationContext = createContext<CitationContextValue | null>(null)

// Provider Props
interface CitationProviderProps {
  content: string
  toolCalls?: Array<{
    id: string
    name: string
    args?: Record<string, unknown>
    status?: string
    result?: string
  }>
  children: ReactNode
}

/**
 * CitationProvider - 解析消息内容中的引用并提供上下文
 */
export function CitationProvider({ content, toolCalls, children }: CitationProviderProps) {
  const registry = useMemo(() => {
    // 从 kb_search 结果的 references 字段直接生成引用注册表
    const kbResult = extractKbSearchResults(toolCalls)
    return generateRegistryFromKbResult(kbResult)
  }, [toolCalls])

  const value = useMemo<CitationContextValue>(() => ({
    registry,
    getCitation: (index: string) => registry.get(index),
  }), [registry])

  return (
    <CitationContext.Provider value={value}>
      {children}
    </CitationContext.Provider>
  )
}

/**
 * Hook: 获取引用上下文
 */
export function useCitationContext() {
  const context = useContext(CitationContext)
  if (!context) {
    throw new Error('useCitationContext must be used within CitationProvider')
  }
  return context
}

/**
 * Hook: 安全获取引用上下文（不抛错）
 */
export function useCitationContextSafe() {
  return useContext(CitationContext)
}
