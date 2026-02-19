import { create } from 'zustand'
import type { Node, Edge, Viewport } from '@xyflow/react'
import type { NodeType, NodeConfig } from '../api/workflow'
import { defaultLabelForNodeType, makeUniqueLabel, normalizeLabel } from '../components/workflow/labelUtils'
import { renameDisplayLabelInReferences } from '../components/workflow/referenceTransform'

export interface WfNodeData {
  nodeType: NodeType
  label: string
  config: NodeConfig | null
  [key: string]: unknown
}

interface HistorySnapshot {
  nodes: Node<WfNodeData>[]
  edges: Edge[]
}

interface WorkflowEditorState {
  // Canvas state
  nodes: Node<WfNodeData>[]
  edges: Edge[]
  viewport: Viewport

  // Selection
  selectedNodeId: string | null
  selectedEdgeId: string | null
  selectedSubflowContainerId: string | null
  selectedSubflowNodeId: string | null
  selectedSubflowEdgeId: string | null
  focusTargetNodeId: string | null
  focusRequestNonce: number

  // Dirty tracking
  isDirty: boolean

  // Undo/redo
  history: HistorySnapshot[]
  historyIndex: number

  // Actions
  setNodes: (nodes: Node<WfNodeData>[]) => void
  setEdges: (edges: Edge[]) => void
  setViewport: (viewport: Viewport) => void
  setSelectedNodeId: (id: string | null) => void
  setSelectedEdgeId: (id: string | null) => void
  setSelectedSubflowSelection: (containerId: string, nodeId: string | null, edgeId: string | null) => void
  clearSelectedSubflowSelection: () => void
  requestFocusNode: (id: string) => void

  addNode: (node: Node<WfNodeData>) => void
  removeNode: (id: string) => void
  updateNodeConfig: (id: string, config: NodeConfig, options?: { pushHistory?: boolean }) => void
  updateNodeLabel: (id: string, label: string) => void
  updateNodePosition: (id: string, x: number, y: number) => void

  addEdge: (edge: Edge) => void
  removeEdge: (id: string) => void

  pushHistory: () => void
  undo: () => void
  redo: () => void
  canUndo: () => boolean
  canRedo: () => boolean

  resetDirty: () => void
  loadWorkflow: (nodes: Node<WfNodeData>[], edges: Edge[], viewport?: Viewport) => void
}

const MAX_HISTORY = 50
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }

export const useWorkflowEditorStore = create<WorkflowEditorState>()((set, get) => ({
  nodes: [],
  edges: [],
  viewport: DEFAULT_VIEWPORT,
  selectedNodeId: null,
  selectedEdgeId: null,
  selectedSubflowContainerId: null,
  selectedSubflowNodeId: null,
  selectedSubflowEdgeId: null,
  focusTargetNodeId: null,
  focusRequestNonce: 0,
  isDirty: false,
  history: [],
  historyIndex: -1,

  setNodes: (nodes) => set({ nodes, isDirty: true }),
  setEdges: (edges) => set({ edges, isDirty: true }),
  setViewport: (viewport) => set({ viewport }),
  setSelectedNodeId: (id) =>
    set({
      selectedNodeId: id,
      selectedEdgeId: null,
      selectedSubflowContainerId: null,
      selectedSubflowNodeId: null,
      selectedSubflowEdgeId: null,
    }),
  setSelectedEdgeId: (id) =>
    set({
      selectedEdgeId: id,
      selectedNodeId: null,
      selectedSubflowContainerId: null,
      selectedSubflowNodeId: null,
      selectedSubflowEdgeId: null,
    }),
  setSelectedSubflowSelection: (containerId, nodeId, edgeId) =>
    set((state) => {
      if (!nodeId && !edgeId) {
        return {
          selectedSubflowContainerId: null,
          selectedSubflowNodeId: null,
          selectedSubflowEdgeId: null,
        }
      }
      if (
        state.selectedSubflowContainerId === containerId &&
        state.selectedSubflowNodeId === nodeId &&
        state.selectedSubflowEdgeId === edgeId &&
        state.selectedNodeId === containerId &&
        state.selectedEdgeId === null
      ) {
        return {}
      }
      return {
        selectedNodeId: containerId,
        selectedEdgeId: null,
        selectedSubflowContainerId: containerId,
        selectedSubflowNodeId: nodeId,
        selectedSubflowEdgeId: edgeId,
      }
    }),
  clearSelectedSubflowSelection: () =>
    set({
      selectedNodeId: null,
      selectedEdgeId: null,
      selectedSubflowContainerId: null,
      selectedSubflowNodeId: null,
      selectedSubflowEdgeId: null,
    }),
  requestFocusNode: (id) =>
    set((state) => ({
      focusTargetNodeId: id,
      focusRequestNonce: state.focusRequestNonce + 1,
    })),

  addNode: (node) => {
    const { nodes } = get()
    const existingLabels = nodes.map((item) => String(item.data.label ?? ''))
    const fallback = defaultLabelForNodeType(node.data.nodeType)
    const base = normalizeLabel(node.data.label) || fallback
    const uniqueLabel = makeUniqueLabel(base, existingLabels)
    const normalizedNode: Node<WfNodeData> = {
      ...node,
      data: {
        ...node.data,
        label: uniqueLabel,
      },
    }
    get().pushHistory()
    set({ nodes: [...nodes, normalizedNode], isDirty: true })
  },

  removeNode: (id) => {
    const {
      nodes,
      edges,
      selectedNodeId,
      selectedSubflowContainerId,
      selectedSubflowNodeId,
      selectedSubflowEdgeId,
    } = get()
    get().pushHistory()
    const shouldClearSubflow = selectedSubflowContainerId === id
    set({
      nodes: nodes.filter((n) => n.id !== id),
      edges: edges.filter((e) => e.source !== id && e.target !== id),
      selectedNodeId: selectedNodeId === id ? null : selectedNodeId,
      selectedSubflowContainerId: shouldClearSubflow ? null : selectedSubflowContainerId,
      selectedSubflowNodeId: shouldClearSubflow ? null : selectedSubflowNodeId,
      selectedSubflowEdgeId: shouldClearSubflow ? null : selectedSubflowEdgeId,
      isDirty: true,
    })
  },

  updateNodeConfig: (id, config, options) => {
    const { nodes } = get()
    if (!nodes.some((node) => node.id === id)) return
    if (options?.pushHistory) {
      get().pushHistory()
    }
    set({
      nodes: nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, config } } : n,
      ),
      isDirty: true,
    })
  },

  updateNodeLabel: (id, label) => {
    const { nodes } = get()
    const target = nodes.find((n) => n.id === id)
    if (!target) return
    const oldLabel = normalizeLabel(target.data.label) || defaultLabelForNodeType(target.data.nodeType)
    const fallback = defaultLabelForNodeType(target.data.nodeType)
    const base = normalizeLabel(label) || fallback
    const existing = nodes
      .filter((n) => n.id !== id)
      .map((n) => String(n.data.label ?? ''))
    const uniqueLabel = makeUniqueLabel(base, existing)
    if (uniqueLabel === String(target.data.label ?? '')) return
    get().pushHistory()
    set({
      nodes: nodes.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, label: uniqueLabel } }
          : {
              ...n,
              data: {
                ...n.data,
                config: renameDisplayLabelInReferences(n.data.config, oldLabel, uniqueLabel) as NodeConfig,
              },
            },
      ),
      isDirty: true,
    })
  },

  updateNodePosition: (id, x, y) => {
    const { nodes } = get()
    set({
      nodes: nodes.map((n) =>
        n.id === id ? { ...n, position: { x, y } } : n,
      ),
    })
  },

  addEdge: (edge) => {
    const { edges } = get()
    get().pushHistory()
    set({ edges: [...edges, edge], isDirty: true })
  },

  removeEdge: (id) => {
    const { edges, selectedEdgeId } = get()
    get().pushHistory()
    set({
      edges: edges.filter((e) => e.id !== id),
      selectedEdgeId: selectedEdgeId === id ? null : selectedEdgeId,
      isDirty: true,
    })
  },

  pushHistory: () => {
    const { nodes, edges, history, historyIndex } = get()
    const trimmed = history.slice(0, historyIndex + 1)
    const next = [...trimmed, { nodes: structuredClone(nodes), edges: structuredClone(edges) }]
    if (next.length > MAX_HISTORY) next.shift()
    set({ history: next, historyIndex: next.length - 1 })
  },

  undo: () => {
    const { history, historyIndex, nodes, edges } = get()
    if (historyIndex < 0) return
    // Save current state if at the tip
    if (historyIndex === history.length - 1) {
      const updated = [...history, { nodes: structuredClone(nodes), edges: structuredClone(edges) }]
      const snapshot = history[historyIndex]
      set({
        nodes: structuredClone(snapshot.nodes),
        edges: structuredClone(snapshot.edges),
        history: updated,
        historyIndex: historyIndex - 1,
        isDirty: true,
      })
    } else {
      const snapshot = history[historyIndex]
      set({
        nodes: structuredClone(snapshot.nodes),
        edges: structuredClone(snapshot.edges),
        historyIndex: historyIndex - 1,
        isDirty: true,
      })
    }
  },

  redo: () => {
    const { history, historyIndex } = get()
    if (historyIndex + 2 >= history.length) return
    const snapshot = history[historyIndex + 2]
    set({
      nodes: structuredClone(snapshot.nodes),
      edges: structuredClone(snapshot.edges),
      historyIndex: historyIndex + 1,
      isDirty: true,
    })
  },

  canUndo: () => get().historyIndex >= 0,
  canRedo: () => get().historyIndex + 2 < get().history.length,

  resetDirty: () => set({ isDirty: false }),

  loadWorkflow: (nodes, edges, viewport) =>
    set({
      nodes,
      edges,
      viewport: viewport ?? DEFAULT_VIEWPORT,
      isDirty: false,
      history: [],
      historyIndex: -1,
      selectedNodeId: null,
      selectedEdgeId: null,
      selectedSubflowContainerId: null,
      selectedSubflowNodeId: null,
      selectedSubflowEdgeId: null,
      focusTargetNodeId: null,
      focusRequestNonce: 0,
    }),
}))
