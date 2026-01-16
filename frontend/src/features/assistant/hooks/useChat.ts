import { useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useChatStore } from '../stores/chat-store'
import { createConversation } from '../api'
import { assistantKeys } from '../queries'
import { ToolCall, SkillCall } from '../types'

interface SSEEvent {
  event: string
  data: Record<string, unknown>
}

/**
 * SSE 解析器类，支持跨 chunk 的事件解析
 */
class SSEParser {
  private buffer = ''

  parse(chunk: string): SSEEvent[] {
    this.buffer += chunk
    const events: SSEEvent[] = []

    // 按双换行分割完整事件
    const parts = this.buffer.split('\n\n')
    // 最后一部分可能不完整，保留在 buffer 中
    this.buffer = parts.pop() || ''

    for (const part of parts) {
      if (!part.trim()) continue

      let eventType = ''
      let eventData = ''

      for (const line of part.split('\n')) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7)
        } else if (line.startsWith('data: ')) {
          eventData = line.slice(6)
        }
      }

      if (eventType && eventData) {
        try {
          events.push({
            event: eventType,
            data: JSON.parse(eventData)
          })
        } catch {
          // ignore parse errors
        }
      }
    }

    return events
  }

  reset() {
    this.buffer = ''
  }
}

export function useChat() {
  const queryClient = useQueryClient()
  const {
    messages,
    isLoading,
    currentConversationId,
    addMessage,
    updateLastMessage,
    addToolCall,
    updateToolCall,
    addSkillCall,
    updateSkillCall,
    startAnalysis,
    updateAnalysis,
    endAnalysis,
    setLoading,
    setConversationId,
  } = useChatStore()

  const abortRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || isLoading) return

    let convId = currentConversationId

    // Create conversation if needed
    if (!convId) {
      try {
        const conv = await createConversation()
        convId = conv.id
        setConversationId(convId)
        // Refresh conversation list immediately when a new conversation is created
        queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
      } catch (err) {
        console.error('Failed to create conversation:', err)
        // 显示错误消息给用户
        addMessage({
          id: Date.now().toString(),
          role: 'assistant',
          content: '*无法创建对话，请检查网络连接后重试*',
          createdAt: Date.now(),
        })
        return
      }
    }

    // Add user message
    const userMsg = {
      id: Date.now().toString(),
      role: 'user' as const,
      content,
      createdAt: Date.now(),
    }
    addMessage(userMsg)
    setLoading(true)

    // Add placeholder for assistant
    const assistantId = (Date.now() + 1).toString()
    addMessage({
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })

    abortRef.current = new AbortController()
    let fullContent = ''
    const sseParser = new SSEParser()

    try {
      const response = await fetch(
        `/api/assistant/conversations/${convId}/chat`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: content }),
          signal: abortRef.current.signal,
        }
      )

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        const events = sseParser.parse(chunk)

        for (const evt of events) {
          if (evt.event === 'content_delta') {
            const delta = evt.data.delta as string
            if (delta) {
              fullContent += delta
              updateLastMessage(fullContent)
            }
          } else if (evt.event === 'error') {
            const error = evt.data.error as string
            fullContent += `\n\n*Error: ${error}*`
            updateLastMessage(fullContent)
          } else if (evt.event === 'tool_call_start') {
            const toolCall: ToolCall = {
              id: evt.data.toolCallId as string,
              name: evt.data.name as string,
              args: evt.data.args as Record<string, unknown>,
              status: 'running',
            }
            addToolCall(toolCall)
          } else if (evt.event === 'tool_call_end') {
            updateToolCall(evt.data.toolCallId as string, {
              status: (evt.data.status as string) === 'completed' ? 'completed' : 'error',
              result: evt.data.result as string,
            })
          } else if (evt.event === 'skill_start') {
            const skillCall: SkillCall = {
              id: evt.data.id as string,
              name: evt.data.name as string,
              status: 'running',
              hidden: Boolean(evt.data.hidden),
            }
            addSkillCall(skillCall)
          } else if (evt.event === 'skill_end') {
            updateSkillCall(evt.data.id as string, {
              status: (evt.data.status as string) === 'completed' ? 'completed' : 'error',
            })
          } else if (evt.event === 'title_updated') {
            // 刷新对话列表以显示新标题
            queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
          } else if (evt.event === 'analysis_start') {
            startAnalysis(evt.data.id as string)
          } else if (evt.event === 'analysis_delta') {
            updateAnalysis(evt.data.id as string, evt.data.delta as string)
          } else if (evt.event === 'analysis_end') {
            endAnalysis(evt.data.id as string)
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        fullContent += '\n\n*Connection error*'
        updateLastMessage(fullContent)
      }
    } finally {
      setLoading(false)
    }
  }, [
    isLoading,
    currentConversationId,
    addMessage,
    updateLastMessage,
    addToolCall,
    updateToolCall,
    addSkillCall,
    updateSkillCall,
    startAnalysis,
    updateAnalysis,
    endAnalysis,
    setLoading,
    setConversationId,
  ])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    setLoading(false)
  }, [setLoading])

  return { messages, isLoading, sendMessage, stop }
}
