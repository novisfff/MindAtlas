import { useState } from 'react'
import { Check, X, Pencil, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField, uiRadius } from '@/components/ui/styles'
import type { EntryType } from '@/types'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'
import { getRandomColor } from '@/lib/colors'

interface TypeRowProps {
  type?: EntryType
  isEditing?: boolean
  isNew?: boolean
  onEdit?: () => void
  onCancel: () => void
  onSave: (data: { code?: string; name: string; color?: string }) => void
  onDelete?: () => void
  isSaving: boolean
}

export function TypeRow({ type, isEditing, isNew, onEdit, onCancel, onSave, onDelete, isSaving }: TypeRowProps) {
  const [code, setCode] = useState(type?.code || '')
  const [name, setName] = useState(type?.name || '')
  const [color, setColor] = useState(() => type?.color || (isNew ? getRandomColor() : '#6B7280'))
  const { t } = useTranslation()

  if (isEditing || isNew) {
    return (
      <div className={cn(uiChrome.inset, 'flex flex-wrap items-center gap-3 p-3')}>
        <input
          type="color"
          value={color}
          onChange={(e) => setColor(e.target.value)}
          className={cn(uiRadius.inset, 'h-10 w-10 cursor-pointer overflow-hidden border border-border bg-background p-1')}
        />
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={t('settings.entryTypes.code')}
          disabled={!isNew}
          className={cn(uiField.input, 'w-32 font-mono')}
        />
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('settings.entryTypes.name')}
          className={cn(uiField.input, 'min-w-[180px] flex-1')}
        />
        <Button
          size="icon"
          variant="secondary"
          onClick={() => onSave({ code: isNew ? code : undefined, name, color })}
          disabled={isSaving || !name.trim() || (isNew && !code.trim())}
          className="text-green-600"
        >
          <Check className="w-4 h-4" />
        </Button>
        <Button size="icon" variant="ghost" onClick={onCancel} className="text-red-600">
          <X className="w-4 h-4" />
        </Button>
      </div>
    )
  }

  if (!type) return null

  return (
    <div className={cn(uiChrome.control, 'group flex items-center gap-3 px-3 py-3')}>
      <div
        className="w-4 h-4 rounded-full shrink-0"
        style={{ backgroundColor: type.color || '#6B7280' }}
      />
      <span className="flex-1 font-medium text-foreground">{type.name}</span>
      <span className="text-xs text-muted-foreground">{type.code}</span>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <Button size="icon" variant="ghost" onClick={onEdit} className="h-8 w-8">
          <Pencil className="w-4 h-4" />
        </Button>
        {onDelete && (
          <Button size="icon" variant="ghost" onClick={onDelete} className="h-8 w-8 text-muted-foreground hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30">
            <Trash2 className="w-4 h-4" />
          </Button>
        )}
      </div>
    </div>
  )
}
