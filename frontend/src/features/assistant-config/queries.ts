import { QueryClient, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as toolsApi from './api/tools'
import * as skillsApi from './api/skills'
import * as workflowsApi from './api/workflows'
import * as agentsApi from './api/agents'
import * as systemBehaviorsApi from './api/system-behaviors'

function upsertById<T extends { id: string }>(items: T[] | undefined, item: T): T[] {
  if (!items || items.length === 0) return [item]
  const existingIndex = items.findIndex((entry) => entry.id === item.id)
  if (existingIndex === -1) return [...items, item]
  const nextItems = [...items]
  nextItems[existingIndex] = item
  return nextItems
}

function syncWorkflowCaches(qc: QueryClient, workflow: workflowsApi.AssistantWorkflow) {
  qc.setQueryData<workflowsApi.AssistantWorkflow[]>(
    ['assistant-workflows'],
    (current) => upsertById(current, workflow),
  )
  qc.setQueryData(['assistant-workflow', workflow.id], workflow)
}

function syncAgentCaches(qc: QueryClient, agent: agentsApi.AssistantAgentProfile) {
  qc.setQueryData<agentsApi.AssistantAgentProfile[]>(
    ['assistant-agents'],
    (current) => upsertById(current, agent),
  )
  qc.setQueryData(['assistant-agent-profile', agent.id], agent)
}

const invalidateAfterSkillReset = (qc: QueryClient) => {
  qc.invalidateQueries({ queryKey: ['assistant-skills'] })
  qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
  qc.invalidateQueries({ queryKey: ['assistant-agents'] })
  qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
  qc.invalidateQueries({ queryKey: ['assistant-workflow'] })
  qc.invalidateQueries({ queryKey: ['assistant-agent-profile'] })
  qc.invalidateQueries({ queryKey: ['assistant-workflow-versions'] })
  qc.invalidateQueries({ queryKey: ['assistant-agent-versions'] })
}

// ==================== Tools ====================

export const useToolsQuery = () =>
  useQuery({
    queryKey: ['assistant-tools'],
    queryFn: toolsApi.getTools,
  })

// 系统工具完整定义（从代码获取）
export const useSystemToolDefinitionsQuery = () =>
  useQuery({
    queryKey: ['system-tool-definitions'],
    queryFn: () => toolsApi.getSystemToolDefinitions({ includeSchema: false }),
  })

export const useUpdateSystemToolEnabledMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      toolsApi.updateSystemToolEnabled(name, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system-tool-definitions'] }),
  })
}

export const useCreateToolMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: toolsApi.createTool,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-tools'] }),
  })
}

export const useUpdateToolMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: toolsApi.UpdateToolRequest }) =>
      toolsApi.updateTool(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-tools'] })
      qc.invalidateQueries({ queryKey: ['system-tool-definitions'] })
    },
  })
}

export const useDeleteToolMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: toolsApi.deleteTool,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-tools'] }),
  })
}

// ==================== Skills ====================

export const useSkillsQuery = () =>
  useQuery({
    queryKey: ['assistant-skills'],
    queryFn: skillsApi.getSkills,
  })

export const useCreateSkillMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: skillsApi.createSkill,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-skills'] }),
  })
}

export const useUpdateSkillMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: skillsApi.UpdateSkillRequest }) =>
      skillsApi.updateSkill(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-skills'] }),
  })
}

export const useDeleteSkillMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: skillsApi.deleteSkill,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-skills'] }),
  })
}

export const useResetSkillMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: skillsApi.resetSkill,
    onSuccess: () => invalidateAfterSkillReset(qc),
  })
}

export const useResetAllSkillsMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: skillsApi.resetAllSkills,
    onSuccess: () => invalidateAfterSkillReset(qc),
  })
}

// ==================== Workflows ====================

export const useWorkflowsQuery = () =>
  useQuery({
    queryKey: ['assistant-workflows'],
    queryFn: workflowsApi.getWorkflows,
  })

export const useCreateWorkflowMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: workflowsApi.createWorkflow,
    onSuccess: (created) => {
      syncWorkflowCaches(qc, created)
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
    },
  })
}

export const useUpdateWorkflowMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: workflowsApi.UpdateWorkflowRequest }) =>
      workflowsApi.updateWorkflowEntity(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
    },
  })
}

export const useDeleteWorkflowMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (
      payload: string | { id: string; confirmRebindSystemBehaviors?: boolean },
    ) => typeof payload === 'string'
      ? workflowsApi.deleteWorkflow(payload)
      : workflowsApi.deleteWorkflow(payload.id, {
          confirmRebindSystemBehaviors: payload.confirmRebindSystemBehaviors,
        }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
    },
  })
}

export const useCopyWorkflowMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: workflowsApi.copyWorkflow,
    onSuccess: (copied) => {
      syncWorkflowCaches(qc, copied)
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
    },
  })
}

// ==================== Agents ====================

export const useAgentProfilesQuery = () =>
  useQuery({
    queryKey: ['assistant-agents'],
    queryFn: agentsApi.getAgentProfiles,
  })

export const useCreateAgentProfileMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: agentsApi.createAgentProfile,
    onSuccess: (created) => {
      syncAgentCaches(qc, created)
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })
}

export const useUpdateAgentProfileMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: agentsApi.UpdateAgentProfileRequest }) =>
      agentsApi.updateAgentProfile(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
    },
  })
}

export const useDeleteAgentProfileMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (
      payload: string | { id: string; confirmRebindSystemBehaviors?: boolean },
    ) => typeof payload === 'string'
      ? agentsApi.deleteAgentProfile(payload)
      : agentsApi.deleteAgentProfile(payload.id, {
          confirmRebindSystemBehaviors: payload.confirmRebindSystemBehaviors,
        }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
    },
  })
}

export const useCopyAgentProfileMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: agentsApi.copyAgentProfile,
    onSuccess: (copied) => {
      syncAgentCaches(qc, copied)
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
    },
  })
}

// ==================== System AI Behaviors ====================

export const useSystemBehaviorsQuery = () =>
  useQuery({
    queryKey: ['assistant-system-behaviors'],
    queryFn: systemBehaviorsApi.getSystemBehaviors,
  })

export const useUpdateSystemBehaviorBindingMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      behaviorKey,
      data,
    }: {
      behaviorKey: string
      data: systemBehaviorsApi.UpdateSystemBehaviorBindingRequest
    }) => systemBehaviorsApi.updateSystemBehaviorBinding(behaviorKey, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })
}

export const useResetSystemBehaviorBindingMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: systemBehaviorsApi.resetSystemBehaviorBinding,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })
}

export const useResetAllSystemBehaviorsMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: systemBehaviorsApi.resetAllSystemBehaviors,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })
}

export const useCreateSystemBehaviorExampleWorkflowMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      behaviorKey,
      data,
    }: {
      behaviorKey: string
      data?: systemBehaviorsApi.CreateSystemBehaviorExampleWorkflowRequest
    }) => systemBehaviorsApi.createSystemBehaviorExampleWorkflow(behaviorKey, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-system-behaviors'] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow'] })
    },
  })
}
