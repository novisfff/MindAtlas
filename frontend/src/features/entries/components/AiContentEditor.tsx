import { Undo } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { MarkdownEditor } from './MarkdownEditor'
import { AiAssistButton } from '@/features/ai'

interface AiContentEditorProps {
  content: string
  onContentChange: (value: string) => void
  prevContent: string | null
  onUndoContent: () => void
  onAiGenerate: () => void
  isAiPending: boolean
  canGenerate: boolean
  disabled?: boolean
}

export function AiContentEditor({
  content,
  onContentChange,
  prevContent,
  onUndoContent,
  onAiGenerate,
  isAiPending,
  canGenerate,
  disabled,
}: AiContentEditorProps) {
  const { t } = useTranslation()

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <label className="block text-sm font-medium">{t('labels.content')}</label>
        <div className="flex items-center gap-3">
          {prevContent !== null && (
            <button
              type="button"
              onClick={onUndoContent}
              className="text-xs flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
            >
              <Undo className="w-3 h-3" />
              {t('entry.form.undoChange')}
            </button>
          )}
          <AiAssistButton
            onClick={onAiGenerate}
            isLoading={isAiPending}
            disabled={disabled || !canGenerate}
          />
        </div>
      </div>
      <MarkdownEditor value={content} onChange={onContentChange} disabled={disabled} />
    </div>
  )
}
