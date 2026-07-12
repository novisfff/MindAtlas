import { useEffect, useState, type CSSProperties } from 'react'
import { useDraggable, useDroppable } from '@dnd-kit/core'
import { Clock, Folder, FolderOpen, GripVertical, Layers3, MoveRight, Pencil, Trash2, Workflow, Bot } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { uiChrome, uiField } from '@/components/ui/styles'
import { SettingsBadge } from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'
import type { AssistantTargetFolder } from '../api/target-folders'

export interface FolderMoveOption {
  id: string | null
  label: string
  disabled?: boolean
}

interface AssistantFolderCardProps {
  folder: AssistantTargetFolder
  pathLabel?: string
  moveOptions: FolderMoveOption[]
  onOpen: () => void
  onEdit: () => void
  onDelete: () => void
  onMove: (parentId: string | null) => void
  disableActions?: boolean
}

const FOLDER_TONES: Record<string, string> = {
  slate: 'from-slate-500/16 via-slate-100/60 to-background border-slate-300/80 text-slate-700 dark:text-slate-200',
  amber: 'from-amber-400/24 via-amber-50/75 to-background border-amber-300/80 text-amber-800 dark:text-amber-100',
  emerald: 'from-emerald-400/20 via-emerald-50/75 to-background border-emerald-300/80 text-emerald-800 dark:text-emerald-100',
  sky: 'from-sky-400/20 via-sky-50/75 to-background border-sky-300/80 text-sky-800 dark:text-sky-100',
  rose: 'from-rose-400/20 via-rose-50/75 to-background border-rose-300/80 text-rose-800 dark:text-rose-100',
}

export function AssistantFolderCard({
  folder,
  pathLabel,
  moveOptions,
  onOpen,
  onEdit,
  onDelete,
  onMove,
  disableActions = false,
}: AssistantFolderCardProps) {
  const { t } = useTranslation()
  const [moveTarget, setMoveTarget] = useState<string | null>(folder.parentId)
  useEffect(() => {
    setMoveTarget(folder.parentId)
  }, [folder.parentId])
  const { setNodeRef: setDroppableRef, isOver } = useDroppable({
    id: `folder-drop:${folder.id}`,
    data: { kind: 'folder-drop', folderId: folder.id },
  })
  const {
    attributes,
    listeners,
    setNodeRef: setDraggableRef,
    transform,
    isDragging,
  } = useDraggable({
    id: `folder:${folder.id}`,
    data: { kind: 'folder', folderId: folder.id },
  })

  const setRefs = (node: HTMLDivElement | null) => {
    setDroppableRef(node)
    setDraggableRef(node)
  }

  const style: CSSProperties = {
    transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined,
    opacity: isDragging ? 0.62 : undefined,
  }
  const tone = FOLDER_TONES[folder.colorToken] ?? FOLDER_TONES.slate
  const totalTargets = folder.workflowCount + folder.agentCount

  return (
    <div
      ref={setRefs}
      style={style}
      className={cn(
        uiChrome.card,
        'group relative overflow-hidden border p-5 transition-all duration-200',
        'bg-gradient-to-br',
        tone,
        isOver && 'scale-[1.01] border-primary/40 ring-4 ring-primary/10',
        isDragging && 'z-20 shadow-xl',
      )}
      onDoubleClick={onOpen}
    >
      <div className="absolute left-8 top-0 h-3 w-24 rounded-b-[16px] bg-current/12" />
      <div className="flex items-start gap-4">
        <button
          type="button"
          {...listeners}
          {...attributes}
          className="mt-1 inline-flex h-9 w-6 cursor-grab items-center justify-center rounded-full text-current/45 transition hover:bg-white/60 hover:text-current active:cursor-grabbing"
          aria-label={t('settings.skills.folderDragHandle')}
        >
          <GripVertical className="h-4 w-4" />
        </button>

        <button
          type="button"
          onClick={onOpen}
          className="flex h-14 w-16 shrink-0 items-center justify-center rounded-[20px] border border-current/12 bg-white/66 text-current shadow-sm transition group-hover:-translate-y-0.5 group-hover:shadow-md dark:bg-white/10"
        >
          <FolderOpen className="h-8 w-8" />
        </button>

        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-2">
              <button
                type="button"
                onClick={onOpen}
                className="block max-w-full truncate text-left text-lg font-semibold text-foreground hover:text-primary"
              >
                {folder.name}
              </button>
              {folder.description ? (
                <p className="line-clamp-2 text-sm leading-6 text-muted-foreground">{folder.description}</p>
              ) : null}
              {pathLabel ? (
                <p className="truncate text-xs text-muted-foreground">{pathLabel}</p>
              ) : null}
            </div>

            <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <Button type="button" variant="ghost" size="icon" onClick={onEdit} disabled={disableActions}>
                <Pencil className="h-4 w-4" />
              </Button>
              <Popover>
                <PopoverTrigger asChild>
                  <Button type="button" variant="ghost" size="icon" disabled={disableActions}>
                    <MoveRight className="h-4 w-4" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-80 space-y-3">
                  <div className="space-y-1">
                    <p className="text-sm font-medium">{t('settings.skills.moveToFolder')}</p>
                    <p className="text-xs text-muted-foreground">{t('settings.skills.moveFolderDescription')}</p>
                  </div>
                  <select
                    className={uiField.select}
                    value={moveTarget ?? '__root__'}
                    onChange={(event) => setMoveTarget(event.target.value === '__root__' ? null : event.target.value)}
                  >
                    {moveOptions.map((option) => (
                      <option key={option.id ?? '__root__'} value={option.id ?? '__root__'} disabled={option.disabled}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <Button type="button" className="w-full" onClick={() => onMove(moveTarget)}>
                    {t('settings.skills.move')}
                  </Button>
                </PopoverContent>
              </Popover>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={onDelete}
                disabled={disableActions}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <SettingsBadge className="gap-1 bg-white/70 dark:bg-white/10">
              <Layers3 className="h-3.5 w-3.5" />
              {t('settings.skills.folderCountSummary', { count: folder.folderCount })}
            </SettingsBadge>
            <SettingsBadge className="gap-1 bg-white/70 dark:bg-white/10">
              <Workflow className="h-3.5 w-3.5" />
              {folder.workflowCount}
            </SettingsBadge>
            <SettingsBadge className="gap-1 bg-white/70 dark:bg-white/10">
              <Bot className="h-3.5 w-3.5" />
              {folder.agentCount}
            </SettingsBadge>
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" />
              {new Date(folder.lastActivityAt).toLocaleDateString()}
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            {Array.from({ length: Math.min(4, Math.max(1, totalTargets + folder.folderCount)) }).map((_, index) => (
              <span
                key={index}
                className="h-2.5 w-10 rounded-full bg-current/15"
                style={{ opacity: 0.7 - index * 0.12 }}
              />
            ))}
            {totalTargets === 0 && folder.folderCount === 0 ? (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Folder className="h-3.5 w-3.5" />
                {t('settings.skills.emptyFolder')}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
