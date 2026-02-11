import { useState, useMemo } from 'react'
import type {
  AssistantSkill,
  CreateSkillRequest,
  UpdateSkillRequest,
  SkillStepInput,
  SkillMode,
  SkillKBConfig,
  OutputFieldSpec,
  OutputFieldType,
} from '../api/skills'

function normalizeOutputFields(
  fields: OutputFieldSpec[] | string[] | null | undefined
): OutputFieldSpec[] {
  if (!fields || fields.length === 0) return []
  return fields.map((f) => {
    if (typeof f === 'string') {
      return { name: f, type: 'string' as OutputFieldType, nullable: false }
    }
    return f as OutputFieldSpec
  })
}

export interface UseSkillFormOptions {
  skill?: AssistantSkill
}

export interface SkillFormState {
  name: string
  description: string
  mode: SkillMode
  systemPrompt: string
  intentExamples: string[]
  agentTools: string[]
  steps: SkillStepInput[]
  kbConfig: SkillKBConfig
  newIntent: string
}

export interface SkillFormActions {
  setName: (name: string) => void
  setDescription: (description: string) => void
  setMode: (mode: SkillMode) => void
  setSystemPrompt: (prompt: string) => void
  setNewIntent: (intent: string) => void
  setKbConfig: (config: SkillKBConfig) => void
  setAgentTools: (tools: string[]) => void
  addIntent: () => void
  removeIntent: (index: number) => void
  addStep: () => void
  removeStep: (index: number) => void
  updateStep: (index: number, updates: Partial<SkillStepInput>) => void
}

export function useSkillForm({ skill }: UseSkillFormOptions) {
  const [name, setName] = useState(skill?.name || '')
  const [description, setDescription] = useState(skill?.description || '')
  const [mode, setMode] = useState<SkillMode>(skill?.mode || 'steps')
  const [systemPrompt, setSystemPrompt] = useState(skill?.systemPrompt || '')
  const [intentExamples, setIntentExamples] = useState<string[]>(
    skill?.intentExamples || []
  )
  const [agentTools, setAgentTools] = useState<string[]>(
    skill?.mode === 'agent' ? (skill?.tools || []) : []
  )
  const [steps, setSteps] = useState<SkillStepInput[]>(
    skill?.steps?.map((s) => ({
      type: s.type,
      instruction: s.instruction || undefined,
      toolName: s.toolName || undefined,
      argsFrom: s.argsFrom || undefined,
      argsTemplate: s.argsTemplate || undefined,
      outputMode: s.outputMode || undefined,
      outputFields: normalizeOutputFields(s.outputFields),
      includeInSummary: s.includeInSummary ?? false,
    })) || [{ type: 'analysis', instruction: '' }]
  )
  const [kbConfig, setKbConfig] = useState<SkillKBConfig>(
    skill?.kbConfig || { enabled: false }
  )
  const [newIntent, setNewIntent] = useState('')

  const derivedTools = useMemo(() => {
    const usedTools = new Set<string>()
    steps.forEach((step) => {
      if (step.type === 'tool' && step.toolName) {
        usedTools.add(step.toolName)
      }
    })
    return Array.from(usedTools)
  }, [steps])

  const addIntent = () => {
    if (newIntent.trim()) {
      setIntentExamples([...intentExamples, newIntent.trim()])
      setNewIntent('')
    }
  }

  const removeIntent = (index: number) => {
    setIntentExamples(intentExamples.filter((_, i) => i !== index))
  }

  const addStep = () => {
    setSteps([...steps, { type: 'tool', toolName: '', includeInSummary: false }])
  }

  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index))
  }

  const updateStep = (index: number, updates: Partial<SkillStepInput>) => {
    setSteps(steps.map((s, i) => (i === index ? { ...s, ...updates } : s)))
  }

  const buildSubmitData = (): CreateSkillRequest | UpdateSkillRequest => ({
    name,
    description,
    intentExamples: intentExamples.length > 0 ? intentExamples : undefined,
    mode,
    ...(mode === 'agent'
      ? {
          tools: agentTools.length > 0 ? agentTools : undefined,
          systemPrompt: systemPrompt || undefined,
          kbConfig,
          steps: undefined,
        }
      : {
          tools: derivedTools.length > 0 ? derivedTools : undefined,
          steps,
          kbConfig,
          systemPrompt: undefined,
        }),
  })

  const isValid =
    !!name &&
    !!description &&
    (mode === 'steps' ? steps.length > 0 : !!systemPrompt.trim())

  return {
    state: {
      name,
      description,
      mode,
      systemPrompt,
      intentExamples,
      agentTools,
      steps,
      kbConfig,
      newIntent,
    },
    derivedTools,
    isValid,
    actions: {
      setName,
      setDescription,
      setMode,
      setSystemPrompt,
      setNewIntent,
      setKbConfig,
      setAgentTools,
      addIntent,
      removeIntent,
      addStep,
      removeStep,
      updateStep,
    },
    buildSubmitData,
  }
}
