import { useState } from 'react'
import { Check, X, Pencil, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField, uiRadius } from '@/components/ui/styles'
import { cn } from '@/lib/utils'
import type { Tag } from '@/types'
import { useTranslation } from 'react-i18next'
import { getRandomColor } from '@/lib/colors'

interface TagRowProps {
  tag?: Tag
  isNew?: boolean
  isEditing?: boolean
  onEdit?: () => void
  onCancel: () => void
  onSave: (data: { name: string; color?: string }) => void
  onDelete?: () => void
  isSaving: boolean
}

export function TagRow({ tag, isNew, isEditing, onEdit, onCancel, onSave, onDelete, isSaving }: TagRowProps) {
  const [name, setName] = useState(tag?.name || '')
  const [color, setColor] = useState(() => tag?.color || (isNew ? getRandomColor() : '#6B7280'))
  const { t } = useTranslation()

  if (isNew || isEditing) {
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
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('settings.tags.placeholder')}
          className={cn(uiField.input, 'min-w-[180px] flex-1')}
          autoFocus
        />
        <Button
          size="icon"
          variant="secondary"
          onClick={() => onSave({ name, color })}
          disabled={isSaving || !name.trim()}
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

  if (!tag) return null

  return (
    <div className={cn(uiChrome.control, 'group flex items-center gap-3 px-3 py-3')}>
      <div
        className="w-4 h-4 rounded-full shrink-0"
        style={{ backgroundColor: tag.color || '#6B7280' }}
      />
      <span className="flex-1 font-medium text-foreground">{tag.name}</span>
      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <Button size="icon" variant="ghost" onClick={onEdit} className="h-8 w-8">
          <Pencil className="w-4 h-4 text-muted-foreground" />
        </Button>
        <Button size="icon" variant="ghost" onClick={onDelete} className="h-8 w-8 text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30">
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>
    </div>
  )
}
