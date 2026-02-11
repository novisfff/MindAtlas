import type { SkillRowProps } from './SkillRowEditor'
import { SkillRowEditor } from './SkillRowEditor'

export type { SkillRowProps }

export function SkillRow(props: SkillRowProps) {
  return <SkillRowEditor {...props} />
}