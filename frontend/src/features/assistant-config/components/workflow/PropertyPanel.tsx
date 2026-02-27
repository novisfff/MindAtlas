import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { useTranslation } from 'react-i18next'
import { useWorkflowEditorStore, type WfNodeData } from '../../stores/workflow-editor-store'
import type {
  ContainerBodyEdge,
  ContainerBodyNode,
  ContainerBodyNodeType,
  NodeConfig,
  NodeType,
} from '../../api/workflow'
import { buildWorkflowReferenceParams } from './variableReferences'
import { NodeHeader } from './property-panel/NodeHeader'
import { StartNodeSettings } from './property-panel/nodes/StartNodeSettings'
import { LlmNodeSettings } from './property-panel/nodes/LlmNodeSettings'
import { ToolNodeSettings } from './property-panel/nodes/ToolNodeSettings'
import { IfElseNodeSettings } from './property-panel/nodes/IfElseNodeSettings'
import {
  ParameterExtractorNodeSettings,
  KnowledgeRetrievalNodeSettings,
  IterationNodeSettings,
  LoopNodeSettings,
} from './property-panel/nodes/OtherNodeSettings'
import { CodeExecutorNodeSettings } from './property-panel/nodes/CodeExecutorNodeSettings'
import { VariableAssignNodeSettings } from './property-panel/nodes/VariableAssignNodeSettings'
import { HumanInLoopNodeSettings } from './property-panel/nodes/HumanInLoopNodeSettings'
import { OutputNodeSettings } from './property-panel/nodes/OutputNodeSettings'
import { useModelsQuery } from '../../../ai-providers/queries'
import { X } from 'lucide-react'
import { defaultLabelForNodeType } from './labelUtils'
import { normalizeIfElseConfig } from './ifElseConfig'
import type { WorkflowToolDefinition } from './types'

interface PropertyPanelProps {
  tools: WorkflowToolDefinition[]
  workflowDescription: string
  onWorkflowDescriptionChange: (value: string) => void
}

type ConfigEditSessionRef = {
  targetKey: string | null
  active: boolean
}

type MainSelectionContext = {
  mode: 'main'
  node: Node<WfNodeData>
}

type SubflowSelectionContext = {
  mode: 'subflow'
  containerNode: Node<WfNodeData>
  containerConfig: Record<string, unknown>
  bodyNodes: ContainerBodyNode[]
  bodyEdges: ContainerBodyEdge[]
  node: ContainerBodyNode
}

type SelectionContext = MainSelectionContext | SubflowSelectionContext

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

function sourceHandlesForSubflowNode(
  nodeType: ContainerBodyNodeType,
  config?: Record<string, unknown> | null,
): string[] {
  if (nodeType === 'if_else') {
    const normalized = normalizeIfElseConfig(config ?? {})
    return [...normalized.branches.map((item) => item.id), normalized.elseHandle || 'else']
  }
  if (nodeType === 'human_in_loop') {
    return ['approved', 'rejected']
  }
  return ['output']
}

function normalizeSubflowSourceHandle(
  sourceNode: ContainerBodyNode | undefined,
  rawSourceHandle: string | null | undefined,
): string {
  const sourceHandle = String(rawSourceHandle ?? '').trim()
  if (!sourceNode) {
    if (!sourceHandle) return 'output'
    return sourceHandle
  }
  const handles = sourceHandlesForSubflowNode(sourceNode.nodeType, sourceNode.config ?? null)
  if (!sourceHandle || sourceHandle === 'output') return handles[0] ?? 'output'
  if (handles.includes(sourceHandle)) return sourceHandle
  return handles[0] ?? 'else'
}

function normalizeSubflowTargetHandle(rawTargetHandle: string | null | undefined): string {
  const targetHandle = String(rawTargetHandle ?? '').trim()
  if (!targetHandle) return 'input'
  return targetHandle
}

function normalizeContainerBodyNodes(config: Record<string, unknown>): ContainerBodyNode[] {
  const raw = (config.bodyNodes ?? config.body_nodes) as unknown
  if (!Array.isArray(raw)) {
    return [
      {
        nodeId: 'start',
        nodeType: 'start',
        label: defaultLabelForNodeType('start'),
        config: null,
      },
    ]
  }

  const nodes = raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const nodeType = String(item.nodeType ?? item.node_type ?? '').trim() as ContainerBodyNodeType
      const fallbackType: ContainerBodyNodeType = nodeType || 'llm'
      return {
        nodeId: String(item.nodeId ?? item.node_id ?? ''),
        nodeType: fallbackType,
        label: String(item.label ?? defaultLabelForNodeType(fallbackType as NodeType)),
        positionX: Number.isFinite(Number(item.positionX ?? item.position_x))
          ? Number(item.positionX ?? item.position_x)
          : undefined,
        positionY: Number.isFinite(Number(item.positionY ?? item.position_y))
          ? Number(item.positionY ?? item.position_y)
          : undefined,
        config: item.config && typeof item.config === 'object' ? (item.config as Record<string, unknown>) : null,
      }
    })
    .filter((item) => item.nodeId)

  if (!nodes.some((item) => item.nodeType === 'start')) {
    return [
      {
        nodeId: 'start',
        nodeType: 'start',
        label: defaultLabelForNodeType('start'),
        config: null,
      },
      ...nodes,
    ]
  }

  return nodes
}

function normalizeContainerBodyEdges(
  config: Record<string, unknown>,
  bodyNodes: ContainerBodyNode[],
): ContainerBodyEdge[] {
  const raw = (config.bodyEdges ?? config.body_edges) as unknown
  if (!Array.isArray(raw)) return []
  const nodeById = new Map(bodyNodes.map((node) => [node.nodeId, node]))

  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const sourceNodeId = String(item.sourceNodeId ?? item.source_node_id ?? '')
      const sourceNode = nodeById.get(sourceNodeId)
      const targetNodeId = String(item.targetNodeId ?? item.target_node_id ?? '')
      return {
        edgeId: String(item.edgeId ?? item.edge_id ?? ''),
        sourceNodeId,
        targetNodeId,
        sourceHandle: normalizeSubflowSourceHandle(
          sourceNode,
          (item.sourceHandle ?? item.source_handle) as string | null | undefined,
        ),
        targetHandle: normalizeSubflowTargetHandle(
          (item.targetHandle ?? item.target_handle) as string | null | undefined,
        ),
        conditionType: (item.conditionType ?? item.condition_type ?? null) as ContainerBodyEdge['conditionType'],
        conditionExpr: (item.conditionExpr ?? item.condition_expr ?? null) as ContainerBodyEdge['conditionExpr'],
        label: (item.label ?? null) as ContainerBodyEdge['label'],
      }
    })
    .filter((item) => (
      item.edgeId &&
      item.sourceNodeId &&
      item.targetNodeId &&
      nodeById.has(item.sourceNodeId) &&
      nodeById.has(item.targetNodeId)
    ))
}

function toSubflowGraphNodes(bodyNodes: ContainerBodyNode[]): Node<WfNodeData>[] {
  return bodyNodes.map((node) => ({
    id: node.nodeId,
    position: {
      x: Number(node.positionX ?? 0),
      y: Number(node.positionY ?? 0),
    },
    data: {
      nodeType: node.nodeType as NodeType,
      label: node.label || defaultLabelForNodeType(node.nodeType as NodeType),
      config: (node.config ?? null) as NodeConfig | null,
    },
  }))
}

function toSubflowGraphEdges(bodyEdges: ContainerBodyEdge[]): Edge[] {
  return bodyEdges.map((edge) => ({
    id: edge.edgeId,
    source: edge.sourceNodeId,
    target: edge.targetNodeId,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
  }))
}

function resolveSelectionContext(
  nodes: Node<WfNodeData>[],
  selectedNodeId: string | null,
  selectedSubflowContainerId: string | null,
  selectedSubflowNodeId: string | null,
): SelectionContext | null {
  if (selectedSubflowContainerId && selectedSubflowNodeId) {
    const containerNode = nodes.find((node) => node.id === selectedSubflowContainerId)
    if (containerNode && (containerNode.data.nodeType === 'iteration' || containerNode.data.nodeType === 'loop')) {
      const containerConfig = (containerNode.data.config ?? {}) as Record<string, unknown>
      const bodyNodes = normalizeContainerBodyNodes(containerConfig)
      const bodyEdges = normalizeContainerBodyEdges(containerConfig, bodyNodes)
      const selectedBodyNode = bodyNodes.find((node) => node.nodeId === selectedSubflowNodeId)
      if (selectedBodyNode) {
        return {
          mode: 'subflow',
          containerNode,
          containerConfig,
          bodyNodes,
          bodyEdges,
          node: selectedBodyNode,
        }
      }
    }
  }

  if (!selectedNodeId) return null
  const selectedNode = nodes.find((node) => node.id === selectedNodeId)
  if (!selectedNode) return null
  return {
    mode: 'main',
    node: selectedNode,
  }
}

export function PropertyPanel({ tools, workflowDescription, onWorkflowDescriptionChange }: PropertyPanelProps) {
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

  const selectionContext = useMemo(
    () => resolveSelectionContext(nodes, selectedNodeId, selectedSubflowContainerId, selectedSubflowNodeId),
    [nodes, selectedNodeId, selectedSubflowContainerId, selectedSubflowNodeId],
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
      const params = buildWorkflowReferenceParams(nodes, edges, selectionContext.node.id, tools)
      if (selectionContext.node.data.nodeType === 'iteration' || selectionContext.node.data.nodeType === 'loop') {
        params.push(...CONTAINER_MENTION_PARAMS)
      }
      return params
    }

    const graphNodes = toSubflowGraphNodes(selectionContext.bodyNodes)
    const graphEdges = toSubflowGraphEdges(selectionContext.bodyEdges)
    const params = buildWorkflowReferenceParams(graphNodes, graphEdges, selectionContext.node.nodeId, tools)
    params.push(...CONTAINER_MENTION_PARAMS)
    return params
  }, [edges, nodes, selectionContext, tools])

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

  const renderContent = () => {
    const commonProps = {
      config,
      onUpdate: handleConfigUpdate,
      mentionParams,
    }

    switch (nodeType) {
      case 'start':
        return (
          <StartNodeSettings
            config={config}
            onUpdate={handleConfigUpdate}
            workflowDescription={workflowDescription}
            onWorkflowDescriptionChange={onWorkflowDescriptionChange}
            isSubflowNode={selectionContext.mode === 'subflow'}
          />
        )
      case 'llm':
        return (
          <LlmNodeSettings
            {...commonProps}
            onChange={handleSingleFieldUpdate}
            knowledgeSourceOptions={knowledgeSourceOptions}
            modelOptions={nodeModelOptions}
          />
        )
      case 'tool':
        return <ToolNodeSettings {...commonProps} tools={tools} />
      case 'if_else':
        return <IfElseNodeSettings {...commonProps} onDeleteBranchEdges={handleDeleteIfElseBranchEdges} />
      case 'parameter_extractor':
        return <ParameterExtractorNodeSettings {...commonProps} modelOptions={nodeModelOptions} />
      case 'knowledge_retrieval':
        return <KnowledgeRetrievalNodeSettings {...commonProps} />
      case 'code_executor':
        return <CodeExecutorNodeSettings {...commonProps} />
      case 'variable_assign':
        return <VariableAssignNodeSettings {...commonProps} />
      case 'human_in_loop':
        return <HumanInLoopNodeSettings {...commonProps} />
      case 'iteration':
        return <IterationNodeSettings {...commonProps} />
      case 'loop':
        return <LoopNodeSettings {...commonProps} />
      case 'output':
        return <OutputNodeSettings {...commonProps} />
      default:
        return <div className="text-sm text-muted-foreground p-4 text-center">{t('settings.skills.noSettingsAvailable')}</div>
    }
  }

  return (
    <div className="w-[400px] h-full flex flex-col bg-slate-50 border-l border-slate-200 shadow-2xl z-20">
      <div className="shrink-0 p-5 bg-white/60 backdrop-blur-sm border-b border-border/40">
        <div className="flex items-center justify-between">
          <div className="flex-1">
            <NodeHeader
              nodeType={nodeType as NodeType}
              label={label}
              onLabelChange={handleLabelChange}
            />
          </div>
          <button
            onClick={() => {
              if (selectionContext.mode === 'subflow') {
                clearSelectedSubflowSelection()
                return
              }
              setSelectedNodeId(null)
            }}
            className="ml-2 p-1.5 text-muted-foreground hover:bg-muted rounded-md transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div
        className="flex-1 overflow-y-auto custom-scrollbar p-6 bg-white"
        onBlurCapture={(event) => {
          const nextTarget = event.relatedTarget
          if (isTextEditableElement(nextTarget)) return
          configEditSessionRef.current = {
            targetKey: targetSessionKey,
            active: false,
          }
        }}
      >
        {renderContent()}
      </div>
    </div>
  )
}
