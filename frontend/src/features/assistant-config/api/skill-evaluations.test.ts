import { describe, expect, it } from 'vitest'
import {
  SKILL_EVAL_BASE,
  buildCreateEvalRunBody,
  type CreateEvalRunRequest,
} from './skill-evaluations'
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

  it('builds CreateEvalRunRequest without client digests', () => {
    const request: CreateEvalRunRequest = {
      requestId: 'req-1',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      prompt: 'hello',
      locale: 'en',
      profileVersionId: 'profile-ver-1',
      mode: 'dataset_scripted',
      datasetVersionIds: ['ds-ver-1'],
      providerFixtureRevision: 'provider-direct-answer@eval-v1',
    }
    const body = buildCreateEvalRunBody(request)
    expect(body).toEqual({
      requestId: 'req-1',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      prompt: 'hello',
      locale: 'en',
      profileVersionId: 'profile-ver-1',
      mode: 'dataset_scripted',
      datasetVersionIds: ['ds-ver-1'],
      providerFixtureRevision: 'provider-direct-answer@eval-v1',
      liveModelId: null,
    })
    expect(body).not.toHaveProperty('subjectContentDigest')
    expect(body).not.toHaveProperty('subjectBindingDigest')
    expect(body).not.toHaveProperty('isolationDigest')
    expect(body).not.toHaveProperty('requiredBuildRevision')
  })

  it('requires published dataset + fixture for dataset_scripted admission', () => {
    expect(() =>
      buildCreateEvalRunBody({
        requestId: 'req-2',
        subjectKind: 'skill_draft',
        subjectAggregateId: 'pkg-1',
        subjectVersionId: 'ver-1',
        prompt: 'hello',
        locale: 'en',
        profileVersionId: 'profile-ver-1',
        mode: 'dataset_scripted',
        datasetVersionIds: [],
      }),
    ).toThrow(/dataset/i)
  })
})
