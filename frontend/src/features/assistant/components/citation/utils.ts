/**
 * 引用标注工具函数和类型定义
 */

// 引用类型
export type CitationType = 'entry' | 'attachment' | 'entity' | 'rel'

// 引用数据结构
export interface CitationData {
  index: string           // 脚注编号 "1", "2", ...
  type: CitationType      // 引用类型
  refId: string           // 引用标识（entryId / entity name / rel source->target）
  text: string            // 脚注显示文本
  sourceData?: {
    // Entry 数据
    entryId?: string
    title?: string
    summary?: string
    content?: string
    // Attachment 数据
    attachmentId?: string
    filename?: string
    // Entity 数据
    name?: string
    entityType?: string
    description?: string
    // Relationship 数据
    source?: string
    target?: string
    keywords?: string
  }
}

// kb_search 返回的引用项
export interface KbReference {
  index: number
  type: 'entry' | 'attachment' | 'entity' | 'rel'
  // entry fields
  entryId?: string
  title?: string
  summary?: string
  // attachment fields
  attachmentId?: string
  filename?: string
  // entity fields
  name?: string
  entityType?: string
  description?: string
  // relationship fields
  source?: string
  target?: string
  keywords?: string
}

// kb_search 返回的数据结构
export interface KbSearchResult {
  mode?: string
  query?: string
  items?: Array<{
    entryId: string
    title: string
    summary: string
    content: string
  }>
  graphContext?: {
    entities?: Array<{
      name: string
      type?: string
      description?: string
      entryId?: string
    }>
    relationships?: Array<{
      source: string
      target: string
      description?: string
      keywords?: string
      entryId?: string
    }>
  }
  references?: KbReference[]
}

// 正则：匹配脚注定义中的自定义标记
// [^1]: [[entry:uuid]] Title
// [^2]: [[attachment:uuid]] filename
// [^2]: [[entity:Name]] Type - Description
// [^3]: [[rel:Source->Target]] Description
const CITATION_DEF_REGEX = /\[\^(\d+)\]:\s*\[\[(entry|attachment|entity|rel):([^\]]+)\]\]\s*(.*)/g

/**
 * 解析 Markdown 内容中的脚注定义，提取引用元数据
 */
export function parseCitationDefinitions(content: string): Map<string, CitationData> {
  const registry = new Map<string, CitationData>()

  if (!content) return registry

  let match: RegExpExecArray | null
  const regex = new RegExp(CITATION_DEF_REGEX.source, 'gm')

  while ((match = regex.exec(content)) !== null) {
    const [, index, type, refId, text] = match

    registry.set(index, {
      index,
      type: type as CitationType,
      refId: refId.trim(),
      text: text.trim(),
    })
  }

  return registry
}

/**
 * 从 toolCalls 中提取 kb_search 结果
 */
export function extractKbSearchResults(toolCalls?: Array<{
  id: string
  name: string
  args?: Record<string, unknown>
  status?: string
  result?: string
}>): KbSearchResult | null {
  if (!toolCalls || toolCalls.length === 0) return null

  // 查找 kb_search 工具调用
  const kbCall = toolCalls.find(tc => tc.name === 'kb_search' && tc.result)
  if (!kbCall || !kbCall.result) return null

  try {
    return JSON.parse(kbCall.result) as KbSearchResult
  } catch {
    return null
  }
}

/**
 * 将引用注册表与 kb_search 结果关联，填充 sourceData
 */
export function hydrateCitations(
  registry: Map<string, CitationData>,
  kbResult: KbSearchResult | null
): Map<string, CitationData> {
  if (!kbResult) return registry

  const items = kbResult.items || []
  const entities = kbResult.graphContext?.entities || []
  const relationships = kbResult.graphContext?.relationships || []

  for (const [index, citation] of registry) {
    switch (citation.type) {
      case 'entry': {
        // 通过 entryId 匹配
        const entry = items.find(item => item.entryId === citation.refId)
        if (entry) {
          citation.sourceData = {
            entryId: entry.entryId,
            title: entry.title,
            summary: entry.summary,
            content: entry.content,
          }
        }
        break
      }
      case 'entity': {
        // 通过 name 匹配（大小写敏感）
        const entity = entities.find(e => e.name === citation.refId)
        if (entity) {
          citation.sourceData = {
            name: entity.name,
            entityType: entity.type,
            description: entity.description,
          }
        }
        break
      }
      case 'rel': {
        // refId 格式: "Source->Target"
        const [source, target] = citation.refId.split('->').map(s => s.trim())
        const rel = relationships.find(r => r.source === source && r.target === target)
        if (rel) {
          citation.sourceData = {
            source: rel.source,
            target: rel.target,
            description: rel.description,
            keywords: rel.keywords,
          }
        }
        break
      }
    }

    registry.set(index, citation)
  }

  return registry
}

/**
 * 从 kb_search 结果的 references 字段直接生成引用注册表
 * 这是新的预编号方式，不再依赖解析 Markdown 脚注定义
 */
export function generateRegistryFromKbResult(
  kbResult: KbSearchResult | null
): Map<string, CitationData> {
  const registry = new Map<string, CitationData>()

  if (!kbResult?.references) return registry

  for (const ref of kbResult.references) {
    const index = String(ref.index)
    const type = ref.type as CitationType

    let refId = ''
    let text = ''

    switch (type) {
      case 'entry':
        refId = ref.entryId || ''
        text = ref.title || ''
        registry.set(index, {
          index,
          type,
          refId,
          text,
          sourceData: {
            entryId: ref.entryId,
            title: ref.title,
            summary: ref.summary,
          },
        })
        break

      case 'attachment':
        refId = ref.attachmentId || ''
        text = ref.filename || ''
        registry.set(index, {
          index,
          type,
          refId,
          text,
          sourceData: {
            entryId: ref.entryId,
            attachmentId: ref.attachmentId,
            filename: ref.filename,
          },
        })
        break

      case 'entity':
        refId = ref.name || ''
        text = `${ref.name}${ref.entityType ? ` (${ref.entityType})` : ''}`
        registry.set(index, {
          index,
          type,
          refId,
          text,
          sourceData: {
            name: ref.name,
            entityType: ref.entityType,
            description: ref.description,
          },
        })
        break

      case 'rel':
        refId = `${ref.source}->${ref.target}`
        text = `${ref.source} → ${ref.target}`
        registry.set(index, {
          index,
          type,
          refId,
          text,
          sourceData: {
            source: ref.source,
            target: ref.target,
            description: ref.description,
            keywords: ref.keywords,
          },
        })
        break
    }
  }

  return registry
}
