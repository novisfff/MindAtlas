import { visit, SKIP } from 'unist-util-visit'
import type { Plugin } from 'unified'

/**
 * Remark plugin to parse `[^n]` citation markers.
 * Transforms them into a custom node type 'citation-marker'
 * that can be rendered by a custom component.
 */
export const remarkCitation: Plugin = () => {
  return (tree) => {
    // 1. Handle regular text nodes (in case remark-gfm didn't catch it or isn't used)
    visit(tree, 'text', (node: any, index: number | null | undefined, parent: any) => {
      const value: string = node?.value ?? ''
      // Match [^n] where n is one or more digits
      const citationRegex = /\[\^(\d+)\]/g

      if (!citationRegex.test(value)) return

      const children: any[] = []
      let lastIndex = 0
      let match: RegExpExecArray | null

      citationRegex.lastIndex = 0

      while ((match = citationRegex.exec(value)) !== null) {
        const start = match.index
        const end = regexLastIndex(match)
        const identifier = match[1]

        // Add text before the citation
        if (start > lastIndex) {
          children.push({ type: 'text', value: value.slice(lastIndex, start) })
        }

        // Add a custom mdast node that mdast->hast will convert using data.hName/hProperties
        children.push({
          type: 'citationMarker',
          data: {
            hName: 'citation-marker',
            hProperties: { identifier },
          },
        })

        lastIndex = end
      }

      // Add remaining text
      if (lastIndex < value.length) {
        children.push({ type: 'text', value: value.slice(lastIndex) })
      }

      if (parent && typeof index === 'number') {
        parent.children.splice(index, 1, ...children)
        return SKIP
      }
    })

    // 2. Handle footnoteReference nodes (created by remark-gfm)
    visit(tree, 'footnoteReference', (node: any, index: number | null | undefined, parent: any) => {
      const identifier: string = node?.identifier ?? node?.label ?? ''
      if (!identifier) return

      const citationNode = {
        type: 'citationMarker',
        data: {
          hName: 'citation-marker',
          hProperties: { identifier },
        },
      }

      if (parent && typeof index === 'number') {
        parent.children.splice(index, 1, citationNode)
        return SKIP
      }
    })
  }
}

// Helper to get the end index of a regex match
function regexLastIndex(match: RegExpExecArray): number {
    return match.index + match[0].length
}
