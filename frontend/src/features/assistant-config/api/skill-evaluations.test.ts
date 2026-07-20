import { describe, expect, it } from 'vitest'
import { SKILL_EVAL_BASE } from './skill-evaluations'
import { assertNoSingleTargetFields } from './main-agent-profiles'

describe('skill-evaluations + profile contracts', () => {
  it('pins eval base path', () => {
    expect(SKILL_EVAL_BASE).toBe('/api/assistant-config/skill-eval')
  })

  it('rejects single-target fields on profile snapshots', () => {
    expect(assertNoSingleTargetFields({ basePrompt: 'x' })).toEqual([])
    expect(assertNoSingleTargetFields({ skillId: 'abc' })).toEqual(['skillId'])
    expect(assertNoSingleTargetFields({ workflowId: 'w', targetType: 'workflow' })).toEqual([
      'workflowId',
      'targetType',
    ])
  })
})
