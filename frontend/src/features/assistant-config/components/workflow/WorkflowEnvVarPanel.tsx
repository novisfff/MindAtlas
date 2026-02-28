import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Pencil, Trash2, X } from 'lucide-react'
import type { WorkflowSessionVar } from '../../api/workflow'
import { EnvVarEditDialog } from './EnvVarEditDialog'

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
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-slate-900/10 backdrop-blur-[1px]"
        onClick={onClose}
      />

      {/* Drawer Panel */}
      <div className="fixed inset-y-0 right-0 z-50 w-[460px] bg-white shadow-2xl border-l border-slate-200 flex flex-col animate-in slide-in-from-right-full duration-200">
        <div className="px-6 py-5 border-b border-slate-100 flex items-start justify-between bg-white">
          <div className="pr-4">
            <h3 className="text-[17px] font-semibold text-slate-800 tracking-tight">{t('settings.skills.envVars.title')}</h3>
            <p className="text-[13px] text-slate-500 mt-1.5 leading-relaxed">{t('settings.skills.envVars.subtitle')}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0 mt-0.5">
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 rounded-md transition-colors"
              title={t('common.close')}
            >
              <X className="w-4 h-4 text-slate-500" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-6 bg-slate-50/50 flex flex-col gap-4">
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
        </div>
      </div>
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
