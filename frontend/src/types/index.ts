export interface Page<T> {
  content: T[]
  pageNumber: number
  pageSize: number
  totalElements: number
  totalPages: number
  last: boolean
  first: boolean
  empty: boolean
}

export interface EntryType {
  id: string
  code: string
  name: string
  description?: string
  color?: string
  icon?: string
  graphEnabled: boolean
  aiEnabled: boolean
  enabled: boolean
}

export interface Tag {
  id: string
  name: string
  color?: string
  description?: string
}

export interface Entry {
  id: string
  title: string
  content?: string
  type: EntryType
  timeMode: 'NONE' | 'POINT' | 'RANGE'
  timeAt?: string
  timeFrom?: string
  timeTo?: string
  summary?: string
  tags?: Tag[]
  createdAt: string
  updatedAt: string
}

export interface RelationType {
  id: string
  code: string
  name: string
  inverseName?: string
  description?: string
  color?: string
  directed: boolean
  enabled: boolean
}

export interface Relation {
  id: string
  sourceEntry: Entry
  targetEntry: Entry
  relationType: RelationType
  description?: string
  createdAt: string
  updatedAt: string
}

export interface Attachment {
  id: string
  entryId: string
  filename: string
  originalFilename: string
  contentType: string
  size: number
  createdAt: string
  indexToKnowledgeGraph?: boolean
  parseStatus?: 'pending' | 'processing' | 'completed' | 'failed'
  parsedAt?: string
  parseLastError?: string
  kgIndexStatus?: 'pending' | 'processing' | 'succeeded' | 'dead'
  kgIndexAttempts?: number
  kgIndexLastError?: string
  kgIndexUpdatedAt?: string
}
