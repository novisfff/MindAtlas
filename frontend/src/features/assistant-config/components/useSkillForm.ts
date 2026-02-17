import { useState } from 'react'
import type {
  AssistantSkill,
  CreateSkillRequest,
  UpdateSkillRequest,
  LanggraphPattern,
  SkillKBConfig,
} from '../api/skills'

export interface UseSkillFormOptions {
  skill?: AssistantSkill
}

export interface SkillFormState {
  name: string
  description: string
  langgraphPattern: LanggraphPattern
  systemPrompt: string
  intentExamples: string[]
  agentTools: string[]
  kbConfig: SkillKBConfig
  newIntent: string
}

export interface SkillFormActions {
  setName: (name: string) => void
  setDescription: (description: string) => void
  setLanggraphPattern: (pattern: LanggraphPattern) => void
  setSystemPrompt: (prompt: string) => void
  setNewIntent: (intent: string) => void
  setKbConfig: (config: SkillKBConfig) => void
  setAgentTools: (tools: string[]) => void
  addIntent: () => void
  removeIntent: (index: number) => void
}

export function useSkillForm({ skill }: UseSkillFormOptions) {
  const [name, setName] = useState(skill?.name || '')
  const [description, setDescription] = useState(skill?.description || '')
  const [langgraphPattern, setLanggraphPattern] = useState<LanggraphPattern>(
    skill?.langgraphPattern === 'workflow_dag' ? 'workflow_dag' : 'agent_loop'
  )
  const [systemPrompt, setSystemPrompt] = useState(skill?.systemPrompt || '')
  const [intentExamples, setIntentExamples] = useState<string[]>(
    skill?.intentExamples || []
  )
  const [agentTools, setAgentTools] = useState<string[]>(skill?.tools || [])
  const [kbConfig, setKbConfig] = useState<SkillKBConfig>(
    skill?.kbConfig || { enabled: false }
  )
  const [newIntent, setNewIntent] = useState('')

  const addIntent = () => {
    if (newIntent.trim()) {
      setIntentExamples([...intentExamples, newIntent.trim()])
      setNewIntent('')
    }
  }

  const removeIntent = (index: number) => {
    setIntentExamples(intentExamples.filter((_, i) => i !== index))
  }

  const buildSubmitData = (): CreateSkillRequest | UpdateSkillRequest => ({
    name,
    description,
    intentExamples: intentExamples.length > 0 ? intentExamples : undefined,
    mode: 'langgraph',
    langgraphPattern,
    tools: agentTools.length > 0 ? agentTools : undefined,
    systemPrompt: langgraphPattern === 'agent_loop' ? (systemPrompt || undefined) : undefined,
    kbConfig: langgraphPattern === 'agent_loop' ? kbConfig : undefined,
  })

  const isValid =
    !!name &&
    !!description &&
    (langgraphPattern !== 'agent_loop' || !!systemPrompt.trim())

  return {
    state: {
      name,
      description,
      langgraphPattern,
      systemPrompt,
      intentExamples,
      agentTools,
      kbConfig,
      newIntent,
    },
    isValid,
    actions: {
      setName,
      setDescription,
      setLanggraphPattern,
      setSystemPrompt,
      setNewIntent,
      setKbConfig,
      setAgentTools,
      addIntent,
      removeIntent,
    },
    buildSubmitData,
  }
}
