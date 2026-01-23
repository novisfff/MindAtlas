export interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  result?: string
  status: 'pending' | 'running' | 'completed' | 'error'
  hidden?: boolean
}

export interface SkillCall {
  id: string
  name: string
  status: 'running' | 'completed' | 'error'
  hidden?: boolean
}

export interface Analysis {
  id: string
  content: string
  status: 'running' | 'completed'
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: ToolCall[]
  skillCalls?: SkillCall[]
  analysis?: Analysis
  toolResults?: { id: string; status: string; result: string }[]
  createdAt: string
  updatedAt: string
}

export interface Conversation {
  id: string
  title?: string
  summary?: string
  isArchived: boolean
  lastMessageAt?: string
  createdAt: string
  updatedAt: string
  messages?: Message[]
}

export interface ConversationList {
  items: Conversation[]
  total: number
}
