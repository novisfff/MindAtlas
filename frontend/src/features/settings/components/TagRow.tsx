import { useState } from 'react'
import { Check, X, Pencil, Trash2 } from 'lucide-react'
import type { Tag } from '@/types'

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
  const [color, setColor] = useState(tag?.color || '#6B7280')

  if (isNew || isEditing) {
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
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Tag name"
          className="flex-1 px-2 py-1 rounded border bg-background"
          autoFocus
        />
        <button
          onClick={() => onSave({ name, color })}
          disabled={isSaving || !name.trim()}
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

  if (!tag) return null

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border hover:bg-muted/50">
      <div
        className="w-4 h-4 rounded-full shrink-0"
        style={{ backgroundColor: tag.color || '#6B7280' }}
      />
      <span className="flex-1 font-medium">{tag.name}</span>
      <button onClick={onEdit} className="p-1.5 rounded hover:bg-muted">
        <Pencil className="w-4 h-4 text-muted-foreground" />
      </button>
      <button onClick={onDelete} className="p-1.5 rounded hover:bg-red-100 text-red-500">
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
  )
}
