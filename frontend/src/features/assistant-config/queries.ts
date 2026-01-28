import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as toolsApi from './api/tools'
import * as skillsApi from './api/skills'

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
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-skills'] }),
  })
}

export const useResetAllSkillsMutation = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: skillsApi.resetAllSkills,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assistant-skills'] }),
  })
}
