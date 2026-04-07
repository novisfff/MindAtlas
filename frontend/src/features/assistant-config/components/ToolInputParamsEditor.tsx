import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'
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
    <div className="flex-1 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-foreground">{t('settings.tools.inputParams')}</h4>
          <div className="text-muted-foreground hover:text-foreground cursor-help" title="Parameters that the AI will generate and pass to this tool">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></svg>
          </div>
        </div>
        <Button type="button" onClick={onAdd} variant="outline" size="sm">
          <Plus className="h-3.5 w-3.5" />
          {t('common.add')}
        </Button>
      </div>

      <div className="space-y-3">
        {inputParams.length === 0 ? (
          <div className="rounded-[12px] border border-dashed border-border/75 bg-muted/20 py-8 text-center text-sm text-muted-foreground">
            {t('settings.tools.noParams', 'No parameters defined')}
          </div>
        ) : (
          <div className="grid gap-3">
            {inputParams.map((param, i) => (
              <div key={i} className={cn(uiChrome.inset, 'group space-y-3 p-4')}>
                <div className="grid gap-3 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.2fr)_160px]">
                  <div className="space-y-2">
                    <label className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Name
                    </label>
                    <input
                      type="text"
                      value={param.name}
                      onChange={(e) => onUpdate(i, { name: e.target.value })}
                      placeholder="key"
                      className={uiField.input}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Description
                    </label>
                    <input
                      type="text"
                      value={param.description || ''}
                      onChange={(e) => onUpdate(i, { description: e.target.value })}
                      placeholder="desc"
                      className={uiField.input}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Type
                    </label>
                    <select
                      value={param.paramType}
                      onChange={(e) => onUpdate(i, { paramType: e.target.value })}
                      className={uiField.select}
                    >
                      {PARAM_TYPES.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="flex items-center justify-end gap-3 border-t border-border/70 pt-3">
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
                    className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
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
