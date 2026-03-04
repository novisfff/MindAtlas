import { create, useStore, StoreApi, createStore } from 'zustand'
import { createContext, useContext, useRef } from 'react'
import { ToolCall, SkillCall, Analysis, HumanApproval, WorkflowStep } from '../types'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCall[]
  skillCalls?: SkillCall[]
  analysisSteps?: Analysis[]
  humanApprovals?: HumanApproval[]
  createdAt: number
}

export interface ChatState {
  messages: ChatMessage[]
  activeWorkflowSteps: WorkflowStep[]
  activeRunId: string | null
  activeRunStatus: string | null
  lastEventSeq: number
  isLoading: boolean
  isOpen: boolean
  currentConversationId: string | null
  addMessage: (message: ChatMessage) => void
  updateLastMessage: (content: string) => void
  setLastMessageId: (id: string) => void
  addToolCall: (toolCall: ToolCall) => void
  updateToolCall: (id: string, updates: Partial<ToolCall>) => void
  addSkillCall: (skillCall: SkillCall) => void
  updateSkillCall: (id: string, updates: Partial<SkillCall>) => void
  upsertHumanApproval: (approval: HumanApproval) => void
  setConversationPendingApprovals: (approvals: HumanApproval[]) => void
  startAnalysis: (id: string) => void
  updateAnalysis: (id: string, delta: string) => void
  endAnalysis: (id: string) => void
  setActiveWorkflowSteps: (steps: WorkflowStep[]) => void
  setActiveRun: (runId: string | null, status?: string | null, lastEventSeq?: number) => void
  setActiveRunStatus: (status: string | null) => void
  setLastEventSeq: (seq: number) => void
  clearActiveRun: () => void
  setLoading: (loading: boolean) => void
  setOpen: (open: boolean) => void
  toggleOpen: () => void
  setConversationId: (id: string | null) => void
  clearMessages: () => void
  setMessages: (messages: ChatMessage[]) => void
}

export const createChatLogic = (set: any): Omit<ChatState, 'no-op'> => ({
  messages: [],
  activeWorkflowSteps: [],
  activeRunId: null,
  activeRunStatus: null,
  lastEventSeq: 0,
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

  setLastMessageId: (id: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        messages[messages.length - 1] = {
          ...messages[messages.length - 1],
          id,
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

  upsertHumanApproval: (approval: HumanApproval) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length === 0) return { messages }

      const targetMessageId = approval.messageId ?? messages[messages.length - 1].id
      const targetIndex = messages.findIndex((item) => item.id === targetMessageId)
      if (targetIndex < 0) {
        return { messages }
      }

      const target = messages[targetIndex]
      const approvals = target.humanApprovals ?? []
      const nextApprovals = [...approvals.filter((item) => item.id !== approval.id), approval]
      messages[targetIndex] = {
        ...target,
        humanApprovals: nextApprovals,
      }
      return { messages }
    }),

  setConversationPendingApprovals: (approvals: HumanApproval[]) =>
    set((state: ChatState) => {
      if (state.messages.length === 0) return state
      const nextMessages = state.messages.map((message) => ({
        ...message,
        humanApprovals: (message.humanApprovals ?? []).filter((item) => item.status === 'pending'),
      }))

      approvals.forEach((approval) => {
        const targetMessageId = approval.messageId
        if (!targetMessageId) return
        const targetIndex = nextMessages.findIndex((item) => item.id === targetMessageId)
        if (targetIndex < 0) return
        const current = nextMessages[targetIndex]
        const existing = current.humanApprovals ?? []
        current.humanApprovals = [...existing.filter((item) => item.id !== approval.id), approval]
      })

      return { messages: nextMessages }
    }),

  startAnalysis: (id: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        messages[messages.length - 1] = {
          ...last,
          analysisSteps: [
            ...(last.analysisSteps || []),
            { id, content: '', status: 'running' },
          ],
        }
      }
      return { messages }
    }),

  updateAnalysis: (id: string, delta: string) =>
    set((state: ChatState) => {
      const messages = [...state.messages]
      if (messages.length > 0) {
        const last = messages[messages.length - 1]
        if (last.analysisSteps) {
          messages[messages.length - 1] = {
            ...last,
            analysisSteps: last.analysisSteps.map((step) =>
              step.id === id
                ? { ...step, content: step.content + delta }
                : step
            ),
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
        if (last.analysisSteps) {
          messages[messages.length - 1] = {
            ...last,
            analysisSteps: last.analysisSteps.map((step) =>
              step.id === id ? { ...step, status: 'completed' } : step
            ),
          }
        }
      }
      return { messages }
    }),

  setActiveWorkflowSteps: (steps: WorkflowStep[]) =>
    set({ activeWorkflowSteps: steps }),

  setActiveRun: (runId: string | null, status: string | null = null, lastEventSeq = 0) =>
    set({
      activeRunId: runId,
      activeRunStatus: status,
      lastEventSeq: Math.max(0, Math.floor(lastEventSeq || 0)),
    }),

  setActiveRunStatus: (status: string | null) =>
    set({ activeRunStatus: status }),

  setLastEventSeq: (seq: number) =>
    set({ lastEventSeq: Math.max(0, Math.floor(seq || 0)) }),

  clearActiveRun: () =>
    set({ activeRunId: null, activeRunStatus: null, lastEventSeq: 0 }),

  setLoading: (isLoading: boolean) => set({ isLoading }),
  setOpen: (isOpen: boolean) => set({ isOpen }),
  toggleOpen: () => set((state: ChatState) => ({ isOpen: !state.isOpen })),
  setConversationId: (id: string | null) => set({ currentConversationId: id }),
  clearMessages: () =>
    set({
      messages: [],
      activeWorkflowSteps: [],
      activeRunId: null,
      activeRunStatus: null,
      lastEventSeq: 0,
      isLoading: false,
    }),
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
