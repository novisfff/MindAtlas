
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useWorkflowEditorStore } from '../../stores/workflow-editor-store'
import type { WorkflowToolDefinition } from './types'
import type { NodeConfig } from '../../api/workflow'
import { buildWorkflowReferenceParams } from './variableReferences'
import { NodeHeader } from './property-panel/NodeHeader'
import { LlmNodeSettings } from './property-panel/nodes/LlmNodeSettings'
import { ToolNodeSettings } from './property-panel/nodes/ToolNodeSettings'
import { IfElseNodeSettings } from './property-panel/nodes/IfElseNodeSettings'
import {
  TemplateNodeSettings,
  ParameterExtractorNodeSettings,
  KnowledgeRetrievalNodeSettings,
  VariableAggregatorNodeSettings
} from './property-panel/nodes/OtherNodeSettings'
import { X } from 'lucide-react'

interface PropertyPanelProps {
  tools: WorkflowToolDefinition[]
}

export function PropertyPanel({ tools }: PropertyPanelProps) {
  const { t } = useTranslation()
  const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)
  const nodes = useWorkflowEditorStore((s) => s.nodes)
  const edges = useWorkflowEditorStore((s) => s.edges)
  const updateNodeConfig = useWorkflowEditorStore((s) => s.updateNodeConfig)
  const updateNodeLabel = useWorkflowEditorStore((s) => s.updateNodeLabel)
  const setSelectedNodeId = useWorkflowEditorStore((s) => s.setSelectedNodeId)

  const selectedNode = selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) : null

  const mentionParams = useMemo(() => {
    if (!selectedNode) return []
    return buildWorkflowReferenceParams(nodes, edges, selectedNode.id, tools)
  }, [edges, nodes, selectedNode, tools])

  if (!selectedNode) {
    return null
  }

  const { nodeType, label } = selectedNode.data as unknown as { nodeType: string; label: string }
  const config = (selectedNode.data.config as Record<string, unknown>) || {}

  const handleConfigUpdate = (updates: Record<string, unknown>) => {
    updateNodeConfig(selectedNode.id, {
      ...config,
      ...updates
    } as NodeConfig)
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
      case 'llm':
        return <LlmNodeSettings {...commonProps} onChange={handleSingleFieldUpdate} />
      case 'tool':
        return <ToolNodeSettings {...commonProps} tools={tools} />
      case 'if_else':
        return <IfElseNodeSettings {...commonProps} />
      case 'template':
        return <TemplateNodeSettings {...commonProps} />
      case 'parameter_extractor':
        return <ParameterExtractorNodeSettings {...commonProps} />
      case 'knowledge_retrieval':
        return <KnowledgeRetrievalNodeSettings {...commonProps} />
      case 'variable_aggregator':
        return <VariableAggregatorNodeSettings {...commonProps} />
      default:
        return <div className="text-sm text-muted-foreground p-4 text-center">{t('settings.skills.noSettingsAvailable')}</div>
    }
  }

  return (
    <div className="w-[360px] h-full flex flex-col bg-white border-l shadow-xl z-20">
      {/* Header Area */}
      <div className="shrink-0 p-4 border-b border-border/40">
        <div className="flex items-center justify-between">
          <div className="flex-1">
            <NodeHeader
              nodeType={nodeType as any}
              label={label}
              onLabelChange={(newLabel) => updateNodeLabel(selectedNode.id, newLabel)}
            />
          </div>
          <button
            onClick={() => setSelectedNodeId(null)}
            className="ml-2 p-1.5 text-muted-foreground hover:bg-muted rounded-md transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

      </div>

      {/* Scrollable Content */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-5">
        {renderContent()}
      </div>
    </div>
  )
}
