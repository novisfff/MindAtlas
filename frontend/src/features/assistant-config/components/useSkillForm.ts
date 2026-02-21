import { useEffect, useMemo, useState } from 'react'
import type {
  AssistantSkill,
  CreateSkillRequest,
  UpdateSkillRequest,
} from '../api/skills'
import type { AssistantExecutableTarget } from './skillTargetOptions'
import { resolveSkillTargetKey } from './skillTargetOptions'

export interface UseSkillFormOptions {
  skill?: AssistantSkill
  availableTargets: AssistantExecutableTarget[]
}

export interface SkillFormState {
  name: string
  description: string
  selectedTargetKey: string
  intentExamples: string[]
  newIntent: string
}

export interface SkillFormActions {
  setName: (name: string) => void
  setDescription: (description: string) => void
  setSelectedTargetKey: (key: string) => void
  setNewIntent: (intent: string) => void
  addIntent: () => void
  removeIntent: (index: number) => void
}

export function useSkillForm({ skill, availableTargets }: UseSkillFormOptions) {
  const firstBindableTarget = availableTargets.find((item) => item.bindable) ?? availableTargets[0]
  const initialTargetKey =
    resolveSkillTargetKey(skill, availableTargets) ??
    firstBindableTarget?.key ??
    ''

  const [name, setName] = useState(skill?.name || '')
  const [description, setDescription] = useState(skill?.description || '')
  const [selectedTargetKey, setSelectedTargetKey] = useState<string>(initialTargetKey)
  const [intentExamples, setIntentExamples] = useState<string[]>(skill?.intentExamples || [])
  const [newIntent, setNewIntent] = useState('')

  const selectedTarget = useMemo(
    () => availableTargets.find((item) => item.key === selectedTargetKey) || null,
    [availableTargets, selectedTargetKey],
  )

  useEffect(() => {
    if (availableTargets.length === 0) return
    const fallback = availableTargets.find((item) => item.bindable) ?? availableTargets[0]
    if (!selectedTargetKey) {
      if (fallback) setSelectedTargetKey(fallback.key)
      return
    }
    const exists = availableTargets.some((item) => item.key === selectedTargetKey)
    const selected = availableTargets.find((item) => item.key === selectedTargetKey)
    if (!exists || (selected && !selected.bindable)) {
      if (fallback) setSelectedTargetKey(fallback.key)
    }
  }, [availableTargets, selectedTargetKey])

  const addIntent = () => {
    if (newIntent.trim()) {
      setIntentExamples([...intentExamples, newIntent.trim()])
      setNewIntent('')
    }
  }

  const removeIntent = (index: number) => {
    setIntentExamples(intentExamples.filter((_, i) => i !== index))
  }

  const hasTarget = !!selectedTarget && selectedTarget.bindable

  const buildSubmitData = (): CreateSkillRequest | UpdateSkillRequest => {
    const payload: CreateSkillRequest | UpdateSkillRequest = {
      name,
      description,
      intentExamples: intentExamples.length > 0 ? intentExamples : undefined,
      mode: 'langgraph',
      targetType: selectedTarget?.type,
      workflowId: undefined,
      agentProfileId: undefined,
    }

    if (selectedTarget?.bindable && selectedTarget.type === 'workflow') {
      payload.workflowId = selectedTarget.id
    }
    if (selectedTarget?.bindable && selectedTarget.type === 'agent') {
      payload.agentProfileId = selectedTarget.id
    }

    return payload
  }

  const isValid = !!name && !!description && hasTarget

  return {
    state: {
      name,
      description,
      selectedTargetKey,
      intentExamples,
      newIntent,
    },
    selectedTarget,
    isValid,
    actions: {
      setName,
      setDescription,
      setSelectedTargetKey,
      setNewIntent,
      addIntent,
      removeIntent,
    } satisfies SkillFormActions,
    buildSubmitData,
  }
}
