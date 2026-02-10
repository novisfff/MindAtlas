import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { InputParam } from '../api/tools'

const PARAM_TYPES = ['string', 'number', 'boolean', 'array', 'object']

interface ToolInputParamsEditorProps {
  inputParams: InputParam[]
  onAdd: () => void
  onRemove: (index: number) => void
  onUpdate: (index: number, updates: Partial<InputParam>) => void
}

export function ToolInputParamsEditor({
  inputParams,
  onAdd,
  onRemove,
  onUpdate,
}: ToolInputParamsEditorProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-4 flex-1">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.inputParams')}</h4>
          <div className="text-muted-foreground hover:text-foreground cursor-help" title="Parameters that the AI will generate and pass to this tool">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></svg>
          </div>
        </div>
        <button
          type="button"
          onClick={onAdd}
          className="text-xs px-2.5 py-1.5 rounded-md border hover:bg-muted bg-background font-medium flex items-center gap-1 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          {t('common.add')}
        </button>
      </div>

      <div className="space-y-3">
        {inputParams.length === 0 ? (
          <div className="text-sm text-muted-foreground italic text-center py-8 border-2 border-dashed rounded-lg bg-muted/20">
            {t('settings.tools.noParams', 'No parameters defined')}
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-1 uppercase tracking-wider">
              <div className="col-span-3">Name</div>
              <div className="col-span-6">Description</div>
              <div className="col-span-3">Type</div>
            </div>
            {inputParams.map((param, i) => (
              <div key={i} className="group relative p-3 rounded-lg border bg-background hover:shadow-sm transition-all space-y-2">
                <div className="grid grid-cols-12 gap-2">
                  <div className="col-span-3">
                    <input
                      type="text"
                      value={param.name}
                      onChange={(e) => onUpdate(i, { name: e.target.value })}
                      placeholder="key"
                      className="w-full px-2 py-1 text-sm rounded border-b border-transparent focus:border-primary bg-transparent focus:bg-muted/10 font-medium"
                    />
                  </div>
                  <div className="col-span-6">
                    <input
                      type="text"
                      value={param.description || ''}
                      onChange={(e) => onUpdate(i, { description: e.target.value })}
                      placeholder="desc"
                      className="w-full px-2 py-1 text-sm rounded border-b border-transparent focus:border-primary bg-transparent focus:bg-muted/10 text-muted-foreground"
                    />
                  </div>
                  <div className="col-span-3">
                    <select
                      value={param.paramType}
                      onChange={(e) => onUpdate(i, { paramType: e.target.value })}
                      className="w-full px-1 py-1 text-xs rounded border-none bg-muted/50 focus:ring-0 cursor-pointer"
                    >
                      {PARAM_TYPES.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="flex items-center justify-end gap-3 pt-1 border-t border-muted/30">
                  <label className="flex items-center gap-1.5 text-xs cursor-pointer select-none text-muted-foreground hover:text-foreground">
                    <input
                      type="checkbox"
                      checked={param.required}
                      onChange={(e) => onUpdate(i, { required: e.target.checked })}
                      className="rounded border-gray-300 text-primary focus:ring-primary"
                    />
                    Required
                  </label>
                  <button
                    type="button"
                    onClick={() => onRemove(i)}
                    className="text-muted-foreground hover:text-destructive transition-colors p-1"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
