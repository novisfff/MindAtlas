import { memo, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowRight, Workflow } from 'lucide-react'
import type { AssistantSkill } from '../../api/skills'
import type { AssistantWorkflow } from '../../api/workflows'
import { deserializeFromSkill, deserializeFromWorkflow } from './serialization'
import { WorkflowReadonlyCanvas } from './WorkflowReadonlyCanvas'

type WorkflowReadonlyPreviewProps = {
  skill?: AssistantSkill
  workflow?: AssistantWorkflow
  onOpenEditor: () => void
}

function WorkflowReadonlyPreviewInner({ skill, workflow, onOpenEditor }: WorkflowReadonlyPreviewProps) {
  const { t } = useTranslation()
  const previewSourceKey = workflow
    ? `workflow:${workflow.id}:${workflow.updatedAt}:${workflow.workflowVersion}`
    : skill
      ? `skill:${skill.id}:${skill.updatedAt}:${skill.workflowVersion ?? 'na'}`
      : 'workflow-preview:empty'

  const { nodes, edges } = useMemo(() => {
    if (!skill && !workflow) {
      return { nodes: [], edges: [] }
    }
    try {
      return workflow
        ? deserializeFromWorkflow(workflow)
        : deserializeFromSkill(skill as AssistantSkill)
    } catch {
      return { nodes: [], edges: [] }
    }
  }, [skill, workflow])

  const isEmpty = nodes.length === 0

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpenEditor}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpenEditor()
        }
      }}
      className="w-full cursor-pointer rounded-[24px] border border-white/80 bg-white/92 p-4 shadow-[0_18px_44px_rgba(15,23,42,0.08)] ring-1 ring-slate-900/5 backdrop-blur-xl transition-colors hover:border-slate-200/90 hover:bg-white/96"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-slate-800">
          <Workflow className="w-4 h-4" />
          <span className="text-sm font-semibold">{t('settings.skills.workflowPreviewTitle')}</span>
        </div>
        <ArrowRight className="w-4 h-4 text-slate-400" />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t('settings.skills.workflowPreviewDesc')}</p>

      {isEmpty ? (
        <div className="mt-3 rounded-lg border border-dashed bg-background/50 px-3 py-6 text-center text-xs text-muted-foreground">
          {t('settings.skills.workflowPreviewEmpty')}
        </div>
      ) : (
        <WorkflowReadonlyCanvas
          key={previewSourceKey}
          className="mt-3 h-56 overflow-hidden rounded-[20px] border border-slate-200/80 bg-[linear-gradient(180deg,rgba(248,250,252,0.88),rgba(255,255,255,0.96))] p-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.75)]"
          nodes={nodes}
          edges={edges}
          variant="thumbnail"
        />
      )}

      <div className="mt-3 text-xs font-medium text-slate-700">{t('settings.skills.workflowPreviewOpen')}</div>
    </div>
  )
}

export const WorkflowReadonlyPreview = memo(WorkflowReadonlyPreviewInner)
