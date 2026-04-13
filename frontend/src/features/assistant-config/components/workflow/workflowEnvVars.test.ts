import { describe, expect, it } from 'vitest'

import {
  getWorkflowEnvVarsFromNodes,
  parseEnvVarDefaultValue,
  toStartConfigWithEnvVars,
} from './workflowEnvVars'

describe('workflowEnvVars', () => {
  it('reads session vars from the start node config', () => {
    const envVars = getWorkflowEnvVarsFromNodes([
      {
        id: 'start',
        type: 'workflowNode',
        position: { x: 0, y: 0 },
        data: {
          nodeType: 'start',
          label: 'Start',
          config: {
            sessionVars: [
              { name: 'counter', type: 'integer', defaultValue: 1 },
              { name: 'enabled', type: 'boolean', defaultValue: true },
            ],
          },
        },
      } as never,
    ])

    expect(envVars).toEqual([
      { name: 'counter', type: 'integer', defaultValue: 1, description: undefined },
      { name: 'enabled', type: 'boolean', defaultValue: true, description: undefined },
    ])
  })

  it('normalizes start config and removes legacy session_vars', () => {
    const config = toStartConfigWithEnvVars(
      {
        inputMode: 'text',
        session_vars: [{ name: 'legacy', type: 'string', defaultValue: 'x' }],
      },
      [
        { name: 'summary', type: 'string', defaultValue: '', description: 'short summary' },
      ],
    )

    expect(config).toEqual({
      inputMode: 'text',
      sessionVars: [
        { name: 'summary', type: 'string', defaultValue: '', description: 'short summary' },
      ],
    })
  })

  it('parses typed default values and rejects invalid integers', () => {
    expect(parseEnvVarDefaultValue('{"count":1}', 'object')).toEqual({ count: 1 })
    expect(parseEnvVarDefaultValue('[1,2,3]', 'array')).toEqual([1, 2, 3])
    expect(parseEnvVarDefaultValue('yes', 'boolean')).toBe(true)
    expect(() => parseEnvVarDefaultValue('3.14', 'integer')).toThrow(
      'default value must be a valid integer',
    )
  })
})
