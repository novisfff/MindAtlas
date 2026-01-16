import { create, useStore, StoreApi, createStore } from 'zustand'
import { createContext, useContext, useRef } from 'react'
import { ToolCall, SkillCall, Analysis } from '../types'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCall[]
  skillCalls?: SkillCall[]
  analysis?: Analysis
  createdAt: number
}

export interface ChatState {
  messages: ChatMessage[]
  isLoading: boolean
  isOpen: boolean
  currentConversationId: string | null
  addMessage: (message: ChatMessage) => void
  updateLastMessage: (content: string) => void
  addToolCall: (toolCall: ToolCall) => void
  updateToolCall: (id: string, updates: Partial<ToolCall>) => void
  addSkillCall: (skillCall: SkillCall) => void
  updateSkillCall: (id: string, updates: Partial<SkillCall>) => void
  startAnalysis: (id: string) => void
  updateAnalysis: (id: string, delta: string) => void
  endAnalysis: (id: string) => void
  setLoading: (loading: boolean) => void
  setOpen: (open: boolean) => void
  toggleOpen: () => void
  setConversationId: (id: string | null) => void
  clearMessages: () => void
  setMessages: (messages: ChatMessage[]) => void
}

export const createChatLogic = (set: any): Omit<ChatState, 'no-op'> => ({
  messages: [],
  isLoading: false,
  isOpen: false,
  currentConversationId: null,

  addMessage: (message: ChatMessage) =>
    set((state: ChatState) => ({ messages: [...state.messages, message] })),

  updateLastMessage: (content: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        messages[messages.length - 1] = {
          ...messages[messages.length - 1],
          content,
        }
      }
      return { messages }
    }),

  addToolCall: (toolCall: ToolCall) =>
    set((state: ChatState) => {
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

  updateToolCall: (id: string, updates: Partial<ToolCall>) =>
    set((state: ChatState) => {
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

  addSkillCall: (skillCall: SkillCall) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        messages[messages.length - 1] = {
          ...last,
          skillCalls: [...(last.skillCalls || []), skillCall],
        }
      }
      return { messages }
    }),

  updateSkillCall: (id: string, updates: Partial<SkillCall>) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        const skillCalls = (last.skillCalls || []).map((sc) =>
          sc.id === id ? { ...sc, ...updates } : sc
        )
        messages[messages.length - 1] = { ...last, skillCalls }
      }
      return { messages }
    }),

  startAnalysis: (id: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        messages[messages.length - 1] = {
          ...last,
          analysis: { id, content: '', status: 'running' },
        }
      }
      return { messages }
    }),

  updateAnalysis: (id: string, delta: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        if (last.analysis && last.analysis.id === id) {
          messages[messages.length - 1] = {
            ...last,
            analysis: {
              ...last.analysis,
              content: last.analysis.content + delta,
            },
          }
        }
      }
      return { messages }
    }),

  endAnalysis: (id: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        if (last.analysis && last.analysis.id === id) {
          messages[messages.length - 1] = {
            ...last,
            analysis: { ...last.analysis, status: 'completed' },
          }
        }
      }
      return { messages }
    }),

  setLoading: (isLoading: boolean) => set({ isLoading }),
  setOpen: (isOpen: boolean) => set({ isOpen }),
  toggleOpen: () => set((state: ChatState) => ({ isOpen: !state.isOpen })),
  setConversationId: (id: string | null) => set({ currentConversationId: id }),
  clearMessages: () => set({ messages: [] }),
  setMessages: (messages: ChatMessage[]) => set({ messages }),
})

// Create the global store (singleton)
export const globalChatStore = createStore<ChatState>(createChatLogic)

// Context for providing local stores
export const ChatStoreContext = createContext<StoreApi<ChatState> | null>(null)

// Hook to consume the store (Context preferred, fallback to Global)
export function useChatStore<T = ChatState>(
  selector: (state: ChatState) => T = (state) => state as unknown as T
): T {
  const store = useContext(ChatStoreContext)
  // If we are inside a provider, use that store. Otherwise use global store.
  const targetStore = store || globalChatStore
  return useStore(targetStore, selector)
}
