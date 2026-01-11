export interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  result?: string
  status: 'pending' | 'running' | 'completed' | 'error'
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: ToolCall[]
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
