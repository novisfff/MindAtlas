export interface SSEEventMessage<T = Record<string, unknown>> {
  event: string
  data: T
}

/**
 * Incremental SSE parser with chunk boundary support.
 */
export class SSEParser {
  private buffer = ''

  parse(chunk: string): SSEEventMessage[] {
    this.buffer += chunk
    const events: SSEEventMessage[] = []

    const blocks = this.buffer.split('\n\n')
    this.buffer = blocks.pop() || ''

    for (const block of blocks) {
      if (!block.trim()) continue

      let eventName = ''
      const dataLines: string[] = []

      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) {
          eventName = line.slice(7).trim()
          continue
        }
        if (line.startsWith('data: ')) {
          dataLines.push(line.slice(6))
        }
      }

      if (!eventName || dataLines.length === 0) continue
      const dataText = dataLines.join('\n')
      try {
        events.push({
          event: eventName,
          data: JSON.parse(dataText),
        })
      } catch {
        // Ignore malformed events
      }
    }

    return events
  }

  reset() {
    this.buffer = ''
  }
}
