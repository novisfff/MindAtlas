import { useState, useEffect } from 'react'
import { Brain, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Analysis } from '../types'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { remarkCitation } from './remark-citation'
import { CitationMarker } from './citation'

interface AnalysisDisplayProps {
  steps: Analysis[]
}

export function AnalysisDisplay({ steps }: AnalysisDisplayProps) {
  const { t } = useTranslation()
  const [isExpanded, setIsExpanded] = useState(false)

  if (!steps || steps.length === 0) return null

  // Aggregate status: running if any step is running
  const isRunning = steps.some(s => s.status === 'running')

  // Combine all step contents with double newline for markdown paragraph separation
  const combinedContent = steps.map(s => s.content).filter(Boolean).join('\n\n')

  // Auto-expand when running, collapse when completed
  useEffect(() => {
    if (isRunning) {
      setIsExpanded(true)
    } else {
      setIsExpanded(false)
    }
  }, [isRunning])

  const toggleExpand = () => setIsExpanded(!isExpanded)

  return (
    <div className="w-full max-w-full overflow-hidden rounded-lg border border-border/50 bg-background/50 text-sm mb-2">
      <div
        className={cn(
          "flex cursor-pointer items-center justify-between px-3 py-2 transition-colors hover:bg-muted/50",
          isExpanded && "border-b border-border/50 bg-muted/30"
        )}
        onClick={toggleExpand}
        role="button"
        aria-expanded={isExpanded}
      >
        <div className="flex items-center gap-2 text-muted-foreground">
          {isRunning ? (
            <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
          ) : (
            <Brain className="h-4 w-4 text-purple-500" />
          )}
          <span className="font-medium">
            {isRunning ? t('pages.assistant.analyzing') : t('pages.assistant.analysis_completed')}
          </span>
        </div>
        <div className="text-muted-foreground/50">
          {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </div>
      </div>

      {isExpanded && (
        <div className="bg-muted/10 px-3 py-3 text-muted-foreground prose prose-sm dark:prose-invert max-w-none prose-p:leading-relaxed prose-pre:p-0 prose-pre:bg-transparent">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkCitation]}
            components={
              {
                code: ({ node, inline, className, children, ...props }: any) => (
                  <code className="bg-black/5 dark:bg-white/10 px-1 py-0.5 rounded font-mono text-xs text-foreground" {...props}>
                    {children}
                  </code>
                ),
                'citation-marker': ({ identifier }: { identifier: string }) => (
                  <CitationMarker identifier={identifier} label={identifier} />
                ),
              } as any
            }
          >
            {combinedContent}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
