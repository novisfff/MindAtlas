import { create } from 'zustand'
import { ToolCall } from '../types'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCall[]
  createdAt: number
}

interface ChatState {
  messages: ChatMessage[]
  isLoading: boolean
  isOpen: boolean
  currentConversationId: string | null
  addMessage: (message: ChatMessage) => void
  updateLastMessage: (content: string) => void
  addToolCall: (toolCall: ToolCall) => void
  updateToolCall: (id: string, updates: Partial<ToolCall>) => void
  setLoading: (loading: boolean) => void
  setOpen: (open: boolean) => void
  toggleOpen: () => void
  setConversationId: (id: string | null) => void
  clearMessages: () => void
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isLoading: false,
  isOpen: false,
  currentConversationId: null,

  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),

  updateLastMessage: (content) =>
    set((state) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        messages[messages.length - 1] = {
          ...messages[messages.length - 1],
          content,
        }
      }
      return { messages }
    }),

  addToolCall: (toolCall) =>
    set((state) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        messages[messages.length - 1] = {
          ...last,
          toolCalls: [...(last.toolCalls || []), toolCall],
        }
      }
      return { messages }
    }),

  updateToolCall: (id, updates) =>
    set((state) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        const toolCalls = (last.toolCalls || []).map((tc) =>
          tc.id === id ? { ...tc, ...updates } : tc
        )
        messages[messages.length - 1] = { ...last, toolCalls }
      }
      return { messages }
    }),

  setLoading: (isLoading) => set({ isLoading }),
  setOpen: (isOpen) => set({ isOpen }),
  toggleOpen: () => set((state) => ({ isOpen: !state.isOpen })),
  setConversationId: (id) => set({ currentConversationId: id }),
  clearMessages: () => set({ messages: [] }),
}))
