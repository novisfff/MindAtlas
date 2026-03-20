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
      className="w-full rounded-xl border border-primary/20 bg-primary/5 p-4 cursor-pointer hover:border-primary/40 hover:bg-primary/10 transition-colors"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-primary">
          <Workflow className="w-4 h-4" />
          <span className="text-sm font-semibold">{t('settings.skills.workflowPreviewTitle')}</span>
        </div>
        <ArrowRight className="w-4 h-4 text-primary/70" />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t('settings.skills.workflowPreviewDesc')}</p>

      {isEmpty ? (
        <div className="mt-3 rounded-lg border border-dashed bg-background/50 px-3 py-6 text-center text-xs text-muted-foreground">
          {t('settings.skills.workflowPreviewEmpty')}
        </div>
      ) : (
        <WorkflowReadonlyCanvas
          className="mt-3 h-56 overflow-hidden rounded-lg border bg-background"
          nodes={nodes}
          edges={edges}
          variant="thumbnail"
        />
      )}

      <div className="mt-3 text-xs font-medium text-primary">{t('settings.skills.workflowPreviewOpen')}</div>
    </div>
  )
}

export const WorkflowReadonlyPreview = memo(WorkflowReadonlyPreviewInner)
