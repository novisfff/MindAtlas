import type { ChangeEvent } from 'react'
/**
 * Policy, budget, and completion fields for skill packages.
 * Declared in mindatlas.yaml; server is the acceptance boundary.
 */
import { useTranslation } from 'react-i18next'

import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

export interface SkillPolicyEditorProps {
  mindatlasYaml: string
  onChange: (yaml: string) => void
  disabled?: boolean
  className?: string
  mode?: 'policy' | 'budgets' | 'completion' | 'applicability' | 'full'
}

const SECTION_HINTS: Record<NonNullable<SkillPolicyEditorProps['mode']>, string> = {
  policy: 'settings.universalSkills.policyHint',
  budgets: 'settings.universalSkills.budgetsHint',
  completion: 'settings.universalSkills.completionHint',
  applicability: 'settings.universalSkills.applicabilityHint',
  full: 'settings.universalSkills.mindatlasHint',
}

export function SkillPolicyEditor({
  mindatlasYaml,
  onChange,
  disabled = false,
  className,
  mode = 'full',
}: SkillPolicyEditorProps) {
  const { t } = useTranslation()

  return (
    <div className={cn('space-y-2', className)}>
      <p className="text-sm text-muted-foreground">{t(SECTION_HINTS[mode])}</p>
      <textarea
        value={mindatlasYaml}
        disabled={disabled}
        onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onChange(e.target.value)}
        spellCheck={false}
        className={cn(uiField.textarea, 'min-h-[220px] font-mono text-xs')}
        aria-label={t('settings.universalSkills.mindatlasYaml')}
      />
      <p className="text-xs text-muted-foreground">{t('settings.universalSkills.serverValidationNote')}</p>
    </div>
  )
}
