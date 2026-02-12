import { useTranslation } from 'react-i18next'
import { CommonRichInput, CommonSelect, Label } from '../CommonInputs'
import { NodeSettingsProps } from './ToolNodeSettings'

export function TemplateNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    return (
        <div className="space-y-4">
            <CommonRichInput
                label={t('settings.skills.nodeTypes.template')}
                value={(config.template as string) ?? ''}
                onChange={(val) => onUpdate({ template: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.templatePlaceholder')}
                rows={6}
                minHeight="120px"
            />
        </div>
    )
}

export function ParameterExtractorNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    return (
        <div className="space-y-4">
            <CommonRichInput
                label={t('settings.skills.extractionInstructions')}
                value={(config.instruction as string) ?? ''}
                onChange={(val) => onUpdate({ instruction: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.extractionPlaceholder')}
                rows={4}
            />
        </div>
    )
}

export function KnowledgeRetrievalNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    return (
        <div className="space-y-4">
            <CommonRichInput
                label={t('settings.skills.retrievalQuery')}
                value={(config.query as string) ?? '{{start.user_input}}'}
                onChange={(val) => onUpdate({ query: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.retrievalQueryPlaceholder')}
                rows={2}
            />

            <div className="space-y-1.5">
                <Label>{t('settings.skills.retrievalTopK')}</Label>
                <input
                    type="number"
                    value={(config.topK as number) ?? 5}
                    onChange={(e) => onUpdate({ topK: parseInt(e.target.value, 10) || 5 })}
                    className="w-full px-3 py-2 text-xs rounded-md border bg-background/50 focus:ring-1 focus:ring-primary/20 focus:border-primary/50 outline-none"
                    min={1}
                    max={20}
                />
            </div>
        </div>
    )
}

export function VariableAggregatorNodeSettings({ config, onUpdate }: NodeSettingsProps) {
    const { t } = useTranslation()
    return (
        <div className="space-y-4">
            <CommonSelect
                label={t('settings.skills.aggregatorStrategy')}
                value={(config.mergeStrategy as string) ?? 'all_required'}
                onChange={(val) => onUpdate({ mergeStrategy: val })}
                options={[
                    { label: t('settings.skills.aggregatorStrategyAll'), value: 'all_required' },
                    { label: t('settings.skills.aggregatorStrategyFirst'), value: 'first_completed' }
                ]}
            />
        </div>
    )
}
