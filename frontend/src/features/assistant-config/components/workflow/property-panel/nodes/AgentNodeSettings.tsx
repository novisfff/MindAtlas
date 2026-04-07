import { Cpu, MessageSquare, Plus, Search, Settings2, Terminal, User, Wrench, X, Wrench as WrenchIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

import { CommonOutputList, CommonRichInput, CommonSelect, CommonSwitch, Label } from '../CommonInputs'
import { NodeSettingsProps } from './ToolNodeSettings'

const DEFAULT_MODEL_VALUE = '__system_default_model__'
const DEFAULT_AGENT_MAX_ITERATIONS = 12

export function AgentNodeSettings({
  config,
  onUpdate,
  mentionParams,
  tools = [],
  modelOptions = [],
}: NodeSettingsProps) {
  const { t } = useTranslation()

  const modelSource = String(config.modelSource ?? 'default') === 'custom' ? 'custom' : 'default'
  const rawModelId = typeof config.modelId === 'string' ? config.modelId : ''
  const isModelInList = modelOptions.some((item) => item.id === rawModelId)
  const modelSelectValue = modelSource === 'custom' && rawModelId ? rawModelId : DEFAULT_MODEL_VALUE
  const modelSelectOptions = [
    { label: t('settings.skills.nodeModelDefault'), value: DEFAULT_MODEL_VALUE },
    ...modelOptions.map((item) => ({ label: item.label, value: item.id })),
    ...(!isModelInList && modelSource === 'custom' && rawModelId
      ? [{ label: `${t('settings.skills.nodeModelCustom')}: ${rawModelId}`, value: rawModelId }]
      : []),
  ]

  const availableTools = useMemo(() => tools.filter((tool) => tool.name !== 'kb_search'), [tools])
  const selectedToolNames = Array.isArray(config.toolNames)
    ? config.toolNames.map((item) => String(item).trim()).filter(Boolean)
    : []
  const knowledgeEnabled = Boolean(config.knowledgeEnabled)
  const knowledgeMode = typeof config.knowledgeMode === 'string' ? config.knowledgeMode : ''
  const rawKnowledgeTopK = config.knowledgeTopK
  const knowledgeTopKValue = typeof rawKnowledgeTopK === 'number' ? String(rawKnowledgeTopK) : ''

  const normalizedMaxIterations = (() => {
    const parsed = Number.parseInt(String(config.maxIterations ?? DEFAULT_AGENT_MAX_ITERATIONS), 10)
    if (Number.isNaN(parsed)) return String(DEFAULT_AGENT_MAX_ITERATIONS)
    return String(Math.min(20, Math.max(1, parsed)))
  })()

  const unselectedTools = useMemo(() => {
    return availableTools.filter((t) => !selectedToolNames.includes(t.name))
  }, [availableTools, selectedToolNames])

  const [popoverOpen, setPopoverOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [maxIterationsInput, setMaxIterationsInput] = useState(normalizedMaxIterations)

  useEffect(() => {
    setMaxIterationsInput(normalizedMaxIterations)
  }, [normalizedMaxIterations])

  const filteredUnselectedTools = useMemo(() => {
    if (!searchQuery.trim()) return unselectedTools
    const query = searchQuery.toLowerCase().trim()
    return unselectedTools.filter((t) => {
      const nameMatch = t.name.toLowerCase().includes(query)
      const displayNameMatch = (t.displayName ?? '').toLowerCase().includes(query)
      const descMatch = (t.description || '').toLowerCase().includes(query)
      return nameMatch || displayNameMatch || descMatch
    })
  }, [unselectedTools, searchQuery])

  const addTool = (toolName: string) => {
    const newToolNames = [...selectedToolNames, toolName]
    onUpdate({ toolNames: newToolNames })
    setPopoverOpen(false)
    setSearchQuery('')
  }

  const removeTool = (toolName: string) => {
    const newToolNames = selectedToolNames.filter((name) => name !== toolName)
    onUpdate({ toolNames: newToolNames })
  }

  return (
    <div className="space-y-4">
      <CommonSelect
        icon={<Cpu className="w-4 h-4" />}
        label={t('settings.skills.nodeModel')}
        value={modelSelectValue}
        onChange={(val) => {
          if (val === DEFAULT_MODEL_VALUE) {
            onUpdate({ modelSource: 'default', modelId: undefined })
            return
          }
          onUpdate({ modelSource: 'custom', modelId: val || undefined })
        }}
        options={modelSelectOptions}
      />

      <div className="space-y-1.5">
        <Label icon={<Terminal className="w-4 h-4" />}>{t('settings.skills.llmSystemPrompt')}</Label>
        <textarea
          value={(config.systemPrompt as string) ?? ''}
          onChange={(e) => onUpdate({ systemPrompt: e.target.value })}
          className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none resize-none min-h-[80px] font-mono shadow-sm transition-all"
          placeholder="You are a helpful assistant..."
        />
      </div>

      <CommonRichInput
        icon={<User className="w-4 h-4" />}
        label={t('settings.skills.agentUserInput')}
        value={(config.userInput as string) ?? '{{start.user_input}}'}
        onChange={(value) => onUpdate({ userInput: value })}
        mentionParams={mentionParams}
        placeholder={t('settings.skills.agentUserInputPlaceholder')}
        rows={3}
      />

      <div className="space-y-3">
        <CommonSwitch
          label={t('settings.skills.agentKnowledgeEnabled')}
          description={t('settings.skills.agentKnowledgeHelp')}
          checked={knowledgeEnabled}
          onChange={(checked) => onUpdate({ knowledgeEnabled: checked })}
        />
        {knowledgeEnabled && (
          <div className="space-y-4 pl-3.5 border-l-2 border-slate-200/60 ml-1.5">
            <CommonSelect
              icon={<Settings2 className="w-4 h-4" />}
              label={t('settings.skills.retrievalMode')}
              value={knowledgeMode}
              onChange={(val) => onUpdate({ knowledgeMode: val || undefined })}
              options={[
                { label: t('settings.skills.retrievalModeFallback'), value: '' },
                { label: 'Hybrid', value: 'hybrid' },
                { label: 'Mix', value: 'mix' },
                { label: 'Naive', value: 'naive' },
                { label: 'Local', value: 'local' },
                { label: 'Global', value: 'global' },
              ]}
            />

            <div className="space-y-1.5">
              <Label icon={<Search className="w-4 h-4" />}>
                {t('settings.skills.retrievalTopK')}
              </Label>
              <input
                type="number"
                value={knowledgeTopKValue}
                onChange={(e) => {
                  const val = e.target.value.trim()
                  if (!val) {
                    onUpdate({ knowledgeTopK: undefined })
                    return
                  }
                  const parsed = Number.parseInt(val, 10)
                  if (Number.isNaN(parsed)) return
                  onUpdate({ knowledgeTopK: Math.max(1, Math.min(50, parsed)) })
                }}
                className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none shadow-sm transition-all placeholder:text-slate-400"
                min={1}
                max={50}
                placeholder={t('settings.skills.retrievalTopKFallback')}
              />
            </div>
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label icon={<Wrench className="w-4 h-4" />}>{t('settings.skills.agentToolNames')}</Label>
        {availableTools.length === 0 ? (
          <div className="text-xs text-slate-500 border border-dashed border-slate-200 rounded-lg px-2.5 py-2.5 bg-slate-50">
            {t('settings.skills.agentNoTools')}
          </div>
        ) : (
          <div className="space-y-2">
            {selectedToolNames.length > 0 ? (
              <div className="space-y-1.5">
                {selectedToolNames.map((toolName) => {
                  const toolInfo = availableTools.find((t) => t.name === toolName)
                  const displayName = toolInfo?.displayName ?? toolName
                  return (
                    <div
                      key={toolName}
                      className="group flex flex-col gap-1.5 rounded-lg border border-slate-200 p-2.5 bg-white shadow-sm transition-all hover:border-slate-300 hover:shadow relative"
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <div className="flex items-center gap-2">
                          <div className="flex items-center justify-center w-6 h-6 rounded bg-indigo-50 text-indigo-500 shrink-0">
                            <WrenchIcon className="w-3.5 h-3.5" />
                          </div>
                          <div className="min-w-0">
                            <span className="block truncate text-[13px] font-medium text-slate-800">
                              {displayName}
                            </span>
                            {displayName !== toolName ? (
                              <code className="block truncate text-[11px] text-slate-500">
                                {toolName}
                              </code>
                            ) : null}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeTool(toolName)}
                          className="text-slate-400 hover:text-red-500 transition-colors p-1 rounded hover:bg-red-50"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                      <div
                        className="text-[12px] text-slate-500 line-clamp-2 pr-6 leading-relaxed pl-[32px]"
                        title={toolInfo?.description || t('settings.tools.noDescription')}
                      >
                        {toolInfo?.description || t('settings.tools.noDescription')}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="text-xs text-slate-500 bg-slate-50 rounded-xl border border-dashed border-slate-200 px-3 py-3 text-center">
                {t('settings.skills.noToolsSelected')}
              </div>
            )}

            {unselectedTools.length > 0 && (
              <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="flex w-full items-center justify-center gap-1.5 py-1.5 px-3 text-sm font-medium text-slate-600 bg-white border border-dashed border-slate-300 rounded-xl hover:bg-slate-50 hover:border-slate-400 hover:text-slate-800 transition-colors cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    {t('common.add')}
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  className="w-[320px] p-0 rounded-lg shadow-xl border-slate-200 overflow-hidden flex flex-col"
                  align="start"
                  sideOffset={6}
                >
                  <div className="p-1.5 border-b border-slate-100 bg-slate-50/50">
                    <div className="relative flex items-center">
                      <Search className="absolute left-2 w-3.5 h-3.5 text-slate-400" />
                      <input
                        type="text"
                        placeholder={t('actions.search') + '...'}
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full pl-7 pr-2 py-1 text-[13px] bg-white border border-slate-200 rounded outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all placeholder:text-slate-400"
                        // Prevent popover from closing when clicking inside the input
                        onClick={(e) => e.stopPropagation()}
                        // Prevent hotkeys from triggering canvas actions
                        onKeyDown={(e) => e.stopPropagation()}
                      />
                    </div>
                  </div>
                  <div className="max-h-[280px] overflow-y-auto flex flex-col gap-0.5 custom-scrollbar p-1.5">
                    {filteredUnselectedTools.length > 0 ? (
                      filteredUnselectedTools.map((tool) => (
                        <button
                          key={tool.name}
                          onClick={() => addTool(tool.name)}
                          className="group flex flex-col text-left p-2 rounded hover:bg-slate-50 transition-all w-full gap-1 border border-transparent hover:border-slate-200 hover:shadow-sm"
                        >
                          <div className="flex items-center gap-2">
                            <div className="flex items-center justify-center w-5 h-5 rounded bg-indigo-50 text-indigo-500 shrink-0">
                              <WrenchIcon className="w-3 h-3" />
                            </div>
                            <span className="text-[13px] font-medium text-slate-800 transition-colors truncate">
                              {tool.displayName ?? tool.name}
                            </span>
                          </div>
                          <span
                            className="text-[12px] text-slate-500 line-clamp-2 leading-tight pl-[28px]"
                            title={tool.description || t('settings.tools.noDescription')}
                          >
                            {tool.description || t('settings.tools.noDescription')}
                          </span>
                        </button>
                      ))
                    ) : (
                      <div className="py-4 text-center text-[13px] text-slate-500">
                        {t('messages.noData')}
                      </div>
                    )}
                  </div>
                </PopoverContent>
              </Popover>
            )}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label icon={<Settings2 className="w-4 h-4" />}>{t('settings.skills.agentMaxIterations')}</Label>
        <input
          type="number"
          value={maxIterationsInput}
          onChange={(e) => {
            const val = e.target.value.trim()
            if (!val) {
              setMaxIterationsInput('')
              return
            }
            setMaxIterationsInput(val)
            const parsed = Number.parseInt(val, 10)
            if (Number.isNaN(parsed)) return
            onUpdate({ maxIterations: Math.max(1, Math.min(20, parsed)) })
          }}
          onBlur={() => {
            const val = maxIterationsInput.trim()
            if (!val) {
              setMaxIterationsInput(String(DEFAULT_AGENT_MAX_ITERATIONS))
              onUpdate({ maxIterations: DEFAULT_AGENT_MAX_ITERATIONS })
              return
            }
            const parsed = Number.parseInt(val, 10)
            const nextValue = Number.isNaN(parsed)
              ? DEFAULT_AGENT_MAX_ITERATIONS
              : Math.max(1, Math.min(20, parsed))
            setMaxIterationsInput(String(nextValue))
            onUpdate({ maxIterations: nextValue })
          }}
          min={1}
          max={20}
          placeholder={String(DEFAULT_AGENT_MAX_ITERATIONS)}
          className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none shadow-sm transition-all"
        />
      </div>

      <CommonOutputList
        icon={<MessageSquare className="w-4 h-4" />}
        label={t('settings.skills.toolOutput')}
        outputs={['response']}
      />
    </div>
  )
}
