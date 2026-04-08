import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'
import { RichMentionInput } from './RichMentionInput'
import type { InputParam } from '../api/tools'

export interface KeyValuePair {
  key: string
  value: string
}

interface KeyValueEditorProps {
  pairs: KeyValuePair[]
  onChange: (pairs: KeyValuePair[]) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
  inputParams?: InputParam[]
}

export function KeyValueEditor({
  pairs,
  onChange,
  keyPlaceholder = 'Key',
  valuePlaceholder = 'Value',
  inputParams = [],
}: KeyValueEditorProps) {
  const { t } = useTranslation()

  const addPair = () => {
    onChange([...pairs, { key: '', value: '' }])
  }

  const removePair = (index: number) => {
    onChange(pairs.filter((_, i) => i !== index))
  }

  const updatePair = (index: number, field: 'key' | 'value', newValue: string) => {
    const newPairs = [...pairs]
    newPairs[index] = { ...newPairs[index], [field]: newValue }
    onChange(newPairs)
  }

  return (
    <div className="space-y-3">
      {pairs.map((pair, index) => (
        <div key={index} className={cn(uiChrome.control, 'flex items-start gap-2 p-2 shadow-none')}>
          <RichMentionInput
            value={pair.key}
            onChange={(val: string) => updatePair(index, 'key', val)}
            inputParams={inputParams}
            placeholder={keyPlaceholder}
            className="flex-1"
            inputClassName={uiField.input}
          />
          <RichMentionInput
            value={pair.value}
            onChange={(val: string) => updatePair(index, 'value', val)}
            inputParams={inputParams}
            placeholder={valuePlaceholder}
            className="flex-1"
            inputClassName={uiField.input}
          />
          <Button
            type="button"
            onClick={() => removePair(index)}
            variant="ghost"
            size="icon"
            className="mt-0.5 h-10 w-10 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button type="button" onClick={addPair} variant="outline" size="sm">
        <Plus className="h-3 w-3" />
        {t('common.add')}
      </Button>
    </div>
  )
}
