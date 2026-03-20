import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Pencil, SlidersHorizontal, Trash2 } from 'lucide-react'
import type { WorkflowSessionVar } from '../../api/workflow'
import { EnvVarEditDialog } from './EnvVarEditDialog'
import { WorkflowEditorSurfaceShell } from './WorkflowEditorSurfaceShell'

interface WorkflowEnvVarPanelProps {
  open: boolean
  envVars: WorkflowSessionVar[]
  onClose: () => void
  onChange: (nextVars: WorkflowSessionVar[]) => void
}

export function WorkflowEnvVarPanel({
  open,
  envVars,
  onClose,
  onChange,
}: WorkflowEnvVarPanelProps) {
  const { t } = useTranslation()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)

  const editingVar = editingIndex !== null ? envVars[editingIndex] ?? null : null
  const existingNames = useMemo(
    () => envVars.map((item) => item.name),
    [envVars],
  )

  if (!open) return null

  return (
    <>
      <WorkflowEditorSurfaceShell
        size="default"
        fluid
        icon={<SlidersHorizontal className="h-4 w-4" />}
        title={t('settings.skills.envVars.title')}
        subtitle={t('settings.skills.envVars.subtitle')}
        onClose={onClose}
        headerActions={(
          <button
            type="button"
            onClick={() => {
              setEditingIndex(null)
              setDialogOpen(true)
            }}
            className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-[11px] font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            <Plus className="h-3.5 w-3.5" />
            {t('settings.skills.envVars.create')}
          </button>
        )}
        bodyClassName="flex min-h-0 flex-1 flex-col gap-4 overflow-auto bg-slate-50/60 p-6"
      >
          {envVars.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center p-8">
              <span className="text-[13px] text-slate-400 mb-6">{t('settings.skills.envVars.empty', '还没有环境变量，点击下方创建。')}</span>
              <button
                type="button"
                onClick={() => {
                  setEditingIndex(null)
                  setDialogOpen(true)
                }}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-5 py-2.5 text-[13px] font-medium text-slate-700 hover:bg-slate-50 hover:text-slate-900 shadow-sm transition-all whitespace-nowrap"
              >
                <Plus className="w-4 h-4 text-slate-500" strokeWidth={2.5} />
                {t('settings.skills.envVars.create')}
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {envVars.map((item, index) => {
                const defaultText = item.type === 'string'
                  ? String(item.defaultValue ?? '')
                  : (() => {
                    try {
                      return JSON.stringify(item.defaultValue)
                    } catch {
                      return String(item.defaultValue ?? '')
                    }
                  })()

                return (
                  <div key={`${item.name}-${index}`} className="group relative rounded-xl border border-slate-200 p-4 bg-slate-50 hover:bg-slate-50/80 transition-colors space-y-3">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-center gap-2 flex-wrap mt-0.5">
                        <code className="text-[13px] font-mono font-bold text-slate-800">{item.name}</code>
                        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-md bg-slate-200/60 text-slate-500 uppercase tracking-wider">
                          {item.type}
                        </span>
                      </div>
                      <div className="opacity-0 group-hover:opacity-100 flex items-center gap-1 transition-opacity">
                        <button
                          type="button"
                          onClick={() => {
                            setEditingIndex(index)
                            setDialogOpen(true)
                          }}
                          className="p-1.5 rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-200 transition-colors"
                          title={t('actions.edit')}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => onChange(envVars.filter((_, idx) => idx !== index))}
                          className="p-1.5 rounded-md text-slate-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                          title={t('actions.delete')}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                    <div className="text-xs text-slate-600 break-all leading-relaxed">
                      <span className="font-semibold text-slate-600 mr-1.5">{t('settings.skills.envVars.defaultLabel')}:</span>
                      {item.defaultValue !== null && item.defaultValue !== undefined && item.defaultValue !== '' ? (
                        <span className="font-mono bg-white border border-slate-200 px-1.5 py-0.5 rounded text-[11px]">{defaultText}</span>
                      ) : (
                        <span className="italic text-slate-400">{t('settings.skills.envVars.noDefault', '无')}</span>
                      )}
                    </div>
                    {item.description ? (
                      <div className="text-xs text-slate-500 mt-1">{item.description}</div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          )}
          {envVars.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setEditingIndex(null)
                setDialogOpen(true)
              }}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border border-dashed border-slate-300 bg-white text-[13px] font-medium text-slate-600 hover:text-slate-900 hover:border-slate-400 hover:bg-slate-50 transition-all mt-4"
            >
              <Plus className="w-4 h-4" strokeWidth={2.5} />
              {t('settings.skills.envVars.create')}
            </button>
          )}
      </WorkflowEditorSurfaceShell>
      <EnvVarEditDialog
        open={dialogOpen}
        mode={editingVar ? 'edit' : 'create'}
        initialValue={editingVar ?? undefined}
        existingNames={existingNames}
        onOpenChange={(nextOpen) => {
          setDialogOpen(nextOpen)
          if (!nextOpen) {
            setEditingIndex(null)
          }
        }}
        onSubmit={(value) => {
          if (editingIndex === null) {
            onChange([...envVars, value])
            return
          }
          const next = [...envVars]
          next[editingIndex] = value
          onChange(next)
        }}
      />
    </>
  )
}
