import { useState } from 'react'
import { Check, X, Pencil, Trash2 } from 'lucide-react'
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
      <div className="flex items-center gap-3 p-3 rounded-lg border bg-muted/50">
        <input
          type="color"
          value={color}
          onChange={(e) => setColor(e.target.value)}
          className="w-8 h-8 rounded cursor-pointer"
        />
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={t('settings.entryTypes.code')}
          disabled={!isNew}
          className="w-32 px-2 py-1 rounded border bg-background font-mono text-sm"
        />
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('settings.entryTypes.name')}
          className="flex-1 px-2 py-1 rounded border bg-background"
        />
        <button
          onClick={() => onSave({ code: isNew ? code : undefined, name, color })}
          disabled={isSaving || !name.trim() || (isNew && !code.trim())}
          className="p-1.5 rounded hover:bg-green-100 text-green-600 disabled:opacity-50"
        >
          <Check className="w-4 h-4" />
        </button>
        <button onClick={onCancel} className="p-1.5 rounded hover:bg-red-100 text-red-600">
          <X className="w-4 h-4" />
        </button>
      </div>
    )
  }

  if (!type) return null

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border hover:bg-muted/50 group">
      <div
        className="w-4 h-4 rounded-full shrink-0"
        style={{ backgroundColor: type.color || '#6B7280' }}
      />
      <span className="flex-1 font-medium">{type.name}</span>
      <span className="text-xs text-muted-foreground">{type.code}</span>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button onClick={onEdit} className="p-1.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground">
          <Pencil className="w-4 h-4" />
        </button>
        {onDelete && (
          <button onClick={onDelete} className="p-1.5 rounded hover:bg-red-100 text-muted-foreground hover:text-red-600">
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  )
}
