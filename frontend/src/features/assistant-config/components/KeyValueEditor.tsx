import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
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
    <div className="space-y-2">
      {pairs.map((pair, index) => (
        <div key={index} className="flex items-start gap-2">
          <RichMentionInput
            value={pair.key}
            onChange={(val: string) => updatePair(index, 'key', val)}
            inputParams={inputParams}
            placeholder={keyPlaceholder}
            className="flex-1"
          />
          <RichMentionInput
            value={pair.value}
            onChange={(val: string) => updatePair(index, 'value', val)}
            inputParams={inputParams}
            placeholder={valuePlaceholder}
            className="flex-1"
          />
          <button
            type="button"
            onClick={() => removePair(index)}
            className="p-1.5 text-muted-foreground hover:text-destructive rounded mt-0.5"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addPair}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <Plus className="w-3 h-3" />
        {t('common.add')}
      </button>
    </div>
  )
}
