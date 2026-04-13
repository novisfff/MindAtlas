import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, type ComponentType } from 'react'
import { useTranslation } from 'react-i18next'
import { useWorkflowEditorStore } from '../../stores/workflow-editor-store'
import type {
  NodeConfig,
  NodeType,
  WorkflowCopilotSelection,
} from '../../api/workflow'
import { buildWorkflowReferenceParams } from './variableReferences'
import { NodeHeader } from './property-panel/NodeHeader'
import { useModelsQuery } from '../../../ai-providers/queries'
import { Sparkles } from 'lucide-react'
import { defaultLabelForNodeType } from './labelUtils'
import {
  normalizeContainerBodyEdges,
  normalizeContainerBodyNodes,
  resolveSelectionContext,
  resolveSelectionContextFromTarget,
  toSubflowGraphEdges,
  toSubflowGraphNodes,
  type PropertyPanelSelectionContext,
  type PropertyPanelSelectionTarget,
} from './propertyPanelSelection'
import type { CallableWorkflowDefinition, WorkflowToolDefinition } from './types'
import { WorkflowEditorSurfaceShell } from './WorkflowEditorSurfaceShell'

function lazyNamed<TModule extends Record<string, ComponentType<any>>, TKey extends keyof TModule>(
  loader: () => Promise<TModule>,
  exportName: TKey,
) {
  return lazy(async () => {
    const mod = await loader()
    return { default: mod[exportName] }
  })
}

const StartNodeSettings = lazyNamed(() => import('./property-panel/nodes/StartNodeSettings'), 'StartNodeSettings')
const AgentNodeSettings = lazyNamed(() => import('./property-panel/nodes/AgentNodeSettings'), 'AgentNodeSettings')
const LlmNodeSettings = lazyNamed(() => import('./property-panel/nodes/LlmNodeSettings'), 'LlmNodeSettings')
const ToolNodeSettings = lazyNamed(() => import('./property-panel/nodes/ToolNodeSettings'), 'ToolNodeSettings')
const IfElseNodeSettings = lazyNamed(() => import('./property-panel/nodes/IfElseNodeSettings'), 'IfElseNodeSettings')
const ParameterExtractorNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/OtherNodeSettings'),
  'ParameterExtractorNodeSettings',
)
const KnowledgeRetrievalNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/OtherNodeSettings'),
  'KnowledgeRetrievalNodeSettings',
)
const IterationNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/OtherNodeSettings'),
  'IterationNodeSettings',
)
const LoopNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/OtherNodeSettings'),
  'LoopNodeSettings',
)
const CodeExecutorNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/CodeExecutorNodeSettings'),
  'CodeExecutorNodeSettings',
)
const HttpRequestNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/HttpRequestNodeSettings'),
  'HttpRequestNodeSettings',
)
const VariableAssignNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/VariableAssignNodeSettings'),
  'VariableAssignNodeSettings',
)
const HumanInLoopNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/HumanInLoopNodeSettings'),
  'HumanInLoopNodeSettings',
)
const OutputNodeSettings = lazyNamed(() => import('./property-panel/nodes/OutputNodeSettings'), 'OutputNodeSettings')
const WorkflowCallNodeSettings = lazyNamed(
  () => import('./property-panel/nodes/WorkflowCallNodeSettings'),
  'WorkflowCallNodeSettings',
)

interface PropertyPanelProps {
  tools: WorkflowToolDefinition[]
  workflows: CallableWorkflowDefinition[]
  workflowDescription: string
  onWorkflowDescriptionChange: (value: string) => void
  onAskAiEdit?: (payload: { title?: string; instruction?: string; selection: WorkflowCopilotSelection }) => void
  selectionTarget?: PropertyPanelSelectionTarget | null
  onClose?: () => void
  readOnly?: boolean
}

type ConfigEditSessionRef = {
  targetKey: string | null
  active: boolean
}

const CONTAINER_MENTION_PARAMS = [
  {
    name: 'container.item',
    referencePath: 'container.item',
    groupKey: 'container',
    groupLabel: 'Container',
    itemLabel: 'item',
    paramType: 'object',
    required: false,
    description: 'Current iteration item',
  },
  {
    name: 'container.index',
    referencePath: 'container.index',
    groupKey: 'container',
    groupLabel: 'Container',
    itemLabel: 'index',
    paramType: 'number',
    required: false,
    description: 'Current iteration index',
  },
]

function stableSerialize(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map((item) => stableSerialize(item)).join(',')}]`
  const record = value as Record<string, unknown>
  const keys = Object.keys(record).sort()
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`).join(',')}}`
}

function collectUpstreamNodeIds(
  nodeIds: string[],
  edges: Array<{ source: string; target: string }>,
  currentNodeId: string,
): string[] {
  const nodeIdSet = new Set(nodeIds)
  const reverseAdj = new Map<string, Set<string>>()
  edges.forEach((edge) => {
    if (!nodeIdSet.has(edge.source) || !nodeIdSet.has(edge.target)) return
    if (!reverseAdj.has(edge.target)) reverseAdj.set(edge.target, new Set<string>())
    reverseAdj.get(edge.target)?.add(edge.source)
  })

  const visited = new Set<string>()
  const queue: string[] = [currentNodeId]
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current) continue
    const sources = reverseAdj.get(current)
    if (!sources) continue
    sources.forEach((source) => {
      if (visited.has(source)) return
      visited.add(source)
      queue.push(source)
    })
  }

  return nodeIds.filter((id) => visited.has(id) && id !== currentNodeId)
}

export function PropertyPanel({
  tools,
  workflows,
  workflowDescription,
  onWorkflowDescriptionChange,
  onAskAiEdit,
  selectionTarget = null,
  onClose,
  readOnly = false,
}: PropertyPanelProps) {
  const { t } = useTranslation()
  const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)
  const selectedSubflowContainerId = useWorkflowEditorStore((s) => s.selectedSubflowContainerId)
  const selectedSubflowNodeId = useWorkflowEditorStore((s) => s.selectedSubflowNodeId)
  const nodes = useWorkflowEditorStore((s) => s.nodes)
  const edges = useWorkflowEditorStore((s) => s.edges)
  const setEdges = useWorkflowEditorStore((s) => s.setEdges)
  const updateNodeConfig = useWorkflowEditorStore((s) => s.updateNodeConfig)
  const updateNodeLabel = useWorkflowEditorStore((s) => s.updateNodeLabel)
  const setSelectedNodeId = useWorkflowEditorStore((s) => s.setSelectedNodeId)
  const clearSelectedSubflowSelection = useWorkflowEditorStore((s) => s.clearSelectedSubflowSelection)
  const { data: llmModels = [] } = useModelsQuery({ modelType: 'llm' })
  const configEditSessionRef = useRef<ConfigEditSessionRef>({ targetKey: null, active: false })

  const selectionContext = useMemo<PropertyPanelSelectionContext | null>(
    () => (
      selectionTarget
        ? resolveSelectionContextFromTarget(nodes, selectionTarget)
        : resolveSelectionContext(nodes, selectedNodeId, selectedSubflowContainerId, selectedSubflowNodeId)
    ),
    [nodes, selectedNodeId, selectedSubflowContainerId, selectedSubflowNodeId, selectionTarget],
  )

  const getContainerSnapshot = useCallback((containerId: string) => {
    const state = useWorkflowEditorStore.getState()
    const containerNode = state.nodes.find((node) => node.id === containerId)
    if (!containerNode) return null
    const containerConfig = (containerNode.data.config ?? {}) as Record<string, unknown>
    const bodyNodes = normalizeContainerBodyNodes(containerConfig)
    const bodyEdges = normalizeContainerBodyEdges(containerConfig, bodyNodes)
    return {
      containerNode,
      containerConfig,
      bodyNodes,
      bodyEdges,
    }
  }, [])

  const targetSessionKey = useMemo(() => {
    if (!selectionContext) return null
    if (selectionContext.mode === 'main') {
      return `main:${selectionContext.node.id}`
    }
    return `subflow:${selectionContext.containerNode.id}:${selectionContext.node.nodeId}`
  }, [selectionContext])

  useEffect(() => {
    if (configEditSessionRef.current.targetKey === targetSessionKey) return
    configEditSessionRef.current = { targetKey: targetSessionKey, active: false }
  }, [targetSessionKey])

  const mentionParams = useMemo(() => {
    if (!selectionContext) return []

    if (selectionContext.mode === 'main') {
      const params = buildWorkflowReferenceParams(nodes, edges, selectionContext.node.id, tools, workflows)
      if (selectionContext.node.data.nodeType === 'iteration' || selectionContext.node.data.nodeType === 'loop') {
        params.push(...CONTAINER_MENTION_PARAMS)
      }
      return params
    }

    const graphNodes = toSubflowGraphNodes(selectionContext.bodyNodes)
    const graphEdges = toSubflowGraphEdges(selectionContext.bodyEdges)
    const params = buildWorkflowReferenceParams(graphNodes, graphEdges, selectionContext.node.nodeId, tools, workflows)
    params.push(...CONTAINER_MENTION_PARAMS)
    return params
  }, [edges, nodes, selectionContext, tools, workflows])

  const knowledgeSourceOptions = useMemo(() => {
    if (!selectionContext) return []

    if (selectionContext.mode === 'main') {
      if (selectionContext.node.data.nodeType !== 'llm') return []
      const nodeIds = nodes.map((node) => node.id)
      const upstreamIds = new Set(
        collectUpstreamNodeIds(
          nodeIds,
          edges.map((edge) => ({ source: edge.source, target: edge.target })),
          selectionContext.node.id,
        ),
      )
      return nodes
        .filter((node) => upstreamIds.has(node.id) && node.data.nodeType === 'knowledge_retrieval')
        .map((node) => ({
          id: node.id,
          label: String(node.data.label ?? '').trim() || node.id,
        }))
    }

    if (selectionContext.node.nodeType !== 'llm') return []
    const nodeIds = selectionContext.bodyNodes.map((node) => node.nodeId)
    const upstreamIds = new Set(
      collectUpstreamNodeIds(
        nodeIds,
        selectionContext.bodyEdges.map((edge) => ({
          source: edge.sourceNodeId,
          target: edge.targetNodeId,
        })),
        selectionContext.node.nodeId,
      ),
    )

    return selectionContext.bodyNodes
      .filter((node) => upstreamIds.has(node.nodeId) && node.nodeType === 'knowledge_retrieval')
      .map((node) => ({
        id: node.nodeId,
        label: String(node.label ?? '').trim() || node.nodeId,
      }))
  }, [edges, nodes, selectionContext])

  const nodeModelOptions = useMemo(
    () =>
      llmModels.map((model) => ({
        id: model.id,
        label: model.name,
      })),
    [llmModels],
  )

  if (!selectionContext) {
    return null
  }

  const nodeType = selectionContext.mode === 'main'
    ? selectionContext.node.data.nodeType
    : selectionContext.node.nodeType
  const label = selectionContext.mode === 'main'
    ? String(selectionContext.node.data.label ?? '')
    : String(selectionContext.node.label ?? '')
  const config = selectionContext.mode === 'main'
    ? ((selectionContext.node.data.config as Record<string, unknown>) || {})
    : ((selectionContext.node.config as Record<string, unknown>) || {})

  const isTextEditableElement = (target: EventTarget | Element | null): boolean => {
    if (!(target instanceof Element)) return false
    if (target instanceof HTMLElement && target.isContentEditable) return true
    if (target.closest('[data-rich-mention-input]')) return true
    return Boolean(target.closest('input:not([type="checkbox"]):not([type="radio"]), textarea, [contenteditable], [role="textbox"]'))
  }

  const handleConfigUpdate = (updates: Record<string, unknown>) => {
    if (readOnly) return
    const textEditing = isTextEditableElement(document.activeElement)
    let shouldPushHistory = false
    if (textEditing) {
      if (!configEditSessionRef.current.active || configEditSessionRef.current.targetKey !== targetSessionKey) {
        shouldPushHistory = true
        configEditSessionRef.current = { targetKey: targetSessionKey, active: true }
      }
    } else {
      shouldPushHistory = true
      configEditSessionRef.current = { targetKey: targetSessionKey, active: false }
    }

    if (selectionContext.mode === 'main') {
      const nextConfig = {
        ...config,
        ...updates,
      }
      if (stableSerialize(config) === stableSerialize(nextConfig)) return
      updateNodeConfig(
        selectionContext.node.id,
        nextConfig as NodeConfig,
        shouldPushHistory ? { pushHistory: true } : undefined,
      )
      return
    }

    const snapshot = getContainerSnapshot(selectionContext.containerNode.id)
    if (!snapshot) return
    const currentBodyNode = snapshot.bodyNodes.find((node) => node.nodeId === selectionContext.node.nodeId)
    if (!currentBodyNode) return

    const previousConfig = ((currentBodyNode.config ?? {}) as Record<string, unknown>)
    const nextNodeConfig = {
      ...previousConfig,
      ...updates,
    }
    if (stableSerialize(previousConfig) === stableSerialize(nextNodeConfig)) return

    const nextBodyNodes = snapshot.bodyNodes.map((node) => (
      node.nodeId === selectionContext.node.nodeId
        ? {
          ...node,
          config: nextNodeConfig,
        }
        : node
    ))

    updateNodeConfig(
      snapshot.containerNode.id,
      {
        ...snapshot.containerConfig,
        bodyNodes: nextBodyNodes,
        bodyEdges: snapshot.bodyEdges,
      } as NodeConfig,
      shouldPushHistory ? { pushHistory: true } : undefined,
    )
  }

  const handleLabelChange = (newLabel: string) => {
    if (readOnly) return
    if (selectionContext.mode === 'main') {
      updateNodeLabel(selectionContext.node.id, newLabel)
      return
    }

    const snapshot = getContainerSnapshot(selectionContext.containerNode.id)
    if (!snapshot) return
    const currentBodyNode = snapshot.bodyNodes.find((node) => node.nodeId === selectionContext.node.nodeId)
    if (!currentBodyNode) return

    const normalizedLabel = newLabel.trim() || defaultLabelForNodeType(currentBodyNode.nodeType as NodeType)
    if (normalizedLabel === currentBodyNode.label) return

    const nextBodyNodes = snapshot.bodyNodes.map((node) => (
      node.nodeId === selectionContext.node.nodeId
        ? {
          ...node,
          label: normalizedLabel,
        }
        : node
    ))

    updateNodeConfig(
      snapshot.containerNode.id,
      {
        ...snapshot.containerConfig,
        bodyNodes: nextBodyNodes,
        bodyEdges: snapshot.bodyEdges,
      } as NodeConfig,
      { pushHistory: true },
    )
  }

  const handleDeleteIfElseBranchEdges = (branchId: string) => {
    if (readOnly) return
    if (nodeType !== 'if_else') return

    if (selectionContext.mode === 'main') {
      const filteredEdges = edges.filter(
        (edge) => !(edge.source === selectionContext.node.id && edge.sourceHandle === branchId),
      )
      if (filteredEdges.length === edges.length) return
      setEdges(filteredEdges)
      return
    }

    const snapshot = getContainerSnapshot(selectionContext.containerNode.id)
    if (!snapshot) return

    const filteredBodyEdges = snapshot.bodyEdges.filter((edge) => !(
      edge.sourceNodeId === selectionContext.node.nodeId &&
      String(edge.sourceHandle ?? 'output') === branchId
    ))
    if (filteredBodyEdges.length === snapshot.bodyEdges.length) return

    updateNodeConfig(
      snapshot.containerNode.id,
      {
        ...snapshot.containerConfig,
        bodyNodes: snapshot.bodyNodes,
        bodyEdges: filteredBodyEdges,
      } as NodeConfig,
    )
  }

  // Legacy adapter for LlmNodeSettings which still uses (field, value)
  const handleSingleFieldUpdate = (field: string, value: unknown) => {
    handleConfigUpdate({ [field]: value })
  }

  const nodeSettingsFallback = (
    <div className="flex min-h-[240px] items-center justify-center rounded-2xl border border-slate-200 bg-slate-50/80 px-4 text-sm text-muted-foreground">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 animate-pulse" />
        <span>{t('messages.loading')}</span>
      </div>
    </div>
  )

  const renderContent = () => {
    const commonProps = {
      config,
      onUpdate: handleConfigUpdate,
      mentionParams,
    }

    switch (nodeType) {
      case 'start':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <StartNodeSettings
              config={config}
              onUpdate={handleConfigUpdate}
              workflowDescription={workflowDescription}
              onWorkflowDescriptionChange={(value) => {
                if (readOnly) return
                onWorkflowDescriptionChange(value)
              }}
              isSubflowNode={selectionContext.mode === 'subflow'}
            />
          </Suspense>
        )
      case 'llm':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <LlmNodeSettings
              {...commonProps}
              onChange={handleSingleFieldUpdate}
              knowledgeSourceOptions={knowledgeSourceOptions}
              modelOptions={nodeModelOptions}
            />
          </Suspense>
        )
      case 'agent':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <AgentNodeSettings {...commonProps} tools={tools} modelOptions={nodeModelOptions} />
          </Suspense>
        )
      case 'tool':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <ToolNodeSettings {...commonProps} tools={tools} />
          </Suspense>
        )
      case 'workflow_call':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <WorkflowCallNodeSettings {...commonProps} workflows={workflows} />
          </Suspense>
        )
      case 'if_else':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <IfElseNodeSettings {...commonProps} onDeleteBranchEdges={handleDeleteIfElseBranchEdges} />
          </Suspense>
        )
      case 'parameter_extractor':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <ParameterExtractorNodeSettings {...commonProps} modelOptions={nodeModelOptions} />
          </Suspense>
        )
      case 'knowledge_retrieval':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <KnowledgeRetrievalNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'code_executor':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <CodeExecutorNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'http_request':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <HttpRequestNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'variable_assign':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <VariableAssignNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'human_in_loop':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <HumanInLoopNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'iteration':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <IterationNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'loop':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <LoopNodeSettings {...commonProps} />
          </Suspense>
        )
      case 'output':
        return (
          <Suspense fallback={nodeSettingsFallback}>
            <OutputNodeSettings {...commonProps} />
          </Suspense>
        )
      default:
        return <div className="text-sm text-muted-foreground p-4 text-center">{t('settings.skills.noSettingsAvailable')}</div>
    }
  }

  const copilotSelection: WorkflowCopilotSelection | null = selectionContext.mode === 'subflow'
    ? {
        scope: 'container',
        nodeIds: [selectionContext.node.nodeId],
        edgeIds: [],
        containerId: selectionContext.containerNode.id,
      }
    : {
        scope: 'selection',
        nodeIds: [selectionContext.node.id],
        edgeIds: [],
      }

  const copilotTitle = selectionContext.mode === 'subflow'
    ? `${selectionContext.containerNode.data.label} / ${selectionContext.node.label}`
    : label

  const copilotInstruction = selectionContext.mode === 'subflow'
    ? t('settings.skills.workflowCopilot.defaultEditInstructionSubflow', {
        targetLabel: copilotTitle,
        nodeType,
        nodeId: selectionContext.node.nodeId,
      })
    : t('settings.skills.workflowCopilot.defaultEditInstruction', {
        targetLabel: copilotTitle,
        nodeType,
        nodeId: selectionContext.node.id,
      })

  return (
    <WorkflowEditorSurfaceShell
      size="narrow"
      fluid
      title={(
        <NodeHeader
          nodeType={nodeType as NodeType}
          label={label}
          onLabelChange={handleLabelChange}
          readOnly={readOnly}
        />
      )}
      onClose={onClose ?? (() => {
        if (selectionContext.mode === 'subflow') {
          clearSelectedSubflowSelection()
          return
        }
        setSelectedNodeId(null)
      })}
      bodyClassName="min-h-0 flex-1 overflow-y-auto bg-white px-6 py-5 custom-scrollbar"
    >
      {readOnly ? (
        <div className="mb-5 rounded-2xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-xs leading-6 text-amber-800">
          {t('settings.skills.systemWorkflowReadonlyDescription')}
        </div>
      ) : null}
      {!readOnly && onAskAiEdit && copilotSelection ? (
        <button
          onClick={() => onAskAiEdit({
            title: copilotTitle,
            instruction: copilotInstruction,
            selection: copilotSelection,
          })}
          className="mb-5 inline-flex items-center gap-2 rounded-2xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-100"
        >
          <Sparkles className="w-3.5 h-3.5" />
          {t('settings.skills.workflowCopilot.editWithAi')}
        </button>
      ) : null}
      <div
        onBlurCapture={(event) => {
          const nextTarget = event.relatedTarget
          if (isTextEditableElement(nextTarget)) return
          configEditSessionRef.current = {
            targetKey: targetSessionKey,
            active: false,
          }
        }}
        onMouseDownCapture={(event) => {
          if (!readOnly) return
          const target = event.target
          if (!(target instanceof Element)) return
          if (!target.closest('input, textarea, select, button, [contenteditable], [role="textbox"], [role="switch"]')) return
          event.preventDefault()
          event.stopPropagation()
        }}
        onClickCapture={(event) => {
          if (!readOnly) return
          const target = event.target
          if (!(target instanceof Element)) return
          if (!target.closest('input, textarea, select, button, [contenteditable], [role="textbox"], [role="switch"]')) return
          event.preventDefault()
          event.stopPropagation()
        }}
        onKeyDownCapture={(event) => {
          if (!readOnly) return
          const target = event.target
          if (!(target instanceof Element)) return
          if (!target.closest('input, textarea, select, button, [contenteditable], [role="textbox"], [role="switch"]')) return
          event.preventDefault()
          event.stopPropagation()
        }}
        onFocusCapture={(event) => {
          if (!readOnly) return
          const target = event.target
          if (!(target instanceof HTMLElement)) return
          if (!target.closest('input, textarea, select, button, [contenteditable], [role="textbox"], [role="switch"]')) return
          window.requestAnimationFrame(() => target.blur())
        }}
      >
        {renderContent()}
      </div>
    </WorkflowEditorSurfaceShell>
  )
}
