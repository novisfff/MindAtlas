import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { buildThumbnailPreviewScene } from './workflowReadonlyPreviewLayout'

function makeNode(id: string, nodeType: string, label?: string, position?: { x: number; y: number }): Node<WfNodeData> {
  return {
    id,
    type: nodeType,
    position: position ?? { x: 0, y: 0 },
    data: {
      nodeType: nodeType as WfNodeData['nodeType'],
      label: label ?? id,
      config: null,
    },
  } as Node<WfNodeData>
}

function makeLinearEdges(nodeIds: string[]): Edge[] {
  return nodeIds.slice(1).map((nodeId, index) => ({
    id: `e${index}`,
    source: nodeIds[index]!,
    target: nodeId,
    sourceHandle: 'output',
    targetHandle: 'input',
  }))
}

describe('buildThumbnailPreviewScene', () => {
  it('uses regular density for small workflows', () => {
    const nodes = ['start', 'llm_1', 'tool_1', 'output'].map((id, index) => makeNode(id, index === 0 ? 'start' : index === 3 ? 'output' : index === 1 ? 'llm' : 'tool'))
    const edges = makeLinearEdges(nodes.map((node) => node.id))

    const scene = buildThumbnailPreviewScene(nodes, edges, { width: 1200, height: 224 })

    expect(scene.density).toBe('regular')
    expect(scene.nodes).toHaveLength(4)
  })

  it('uses compact density for medium workflows', () => {
    const nodes = Array.from({ length: 8 }, (_, index) => makeNode(`n${index}`, index === 0 ? 'start' : index === 7 ? 'output' : 'tool'))
    const edges = makeLinearEdges(nodes.map((node) => node.id))

    const scene = buildThumbnailPreviewScene(nodes, edges, { width: 1000, height: 224 })

    expect(scene.density).toBe('compact')
  })

  it('uses dense density for large workflows', () => {
    const nodes = Array.from({ length: 18 }, (_, index) => makeNode(`n${index}`, index === 0 ? 'start' : index === 17 ? 'output' : 'tool'))
    const edges = makeLinearEdges(nodes.map((node) => node.id))

    const scene = buildThumbnailPreviewScene(nodes, edges, { width: 1000, height: 224 })

    expect(scene.density).toBe('dense')
  })

  it('ignores sparse saved coordinates when computing thumbnail layout', () => {
    const denseNodes = [
      makeNode('start', 'start', 'Start', { x: 0, y: 0 }),
      makeNode('tool_a', 'tool', 'A', { x: 200, y: 0 }),
      makeNode('tool_b', 'tool', 'B', { x: 400, y: 0 }),
      makeNode('output', 'output', 'Output', { x: 600, y: 0 }),
    ]
    const sparseNodes = [
      makeNode('start', 'start', 'Start', { x: 0, y: 0 }),
      makeNode('tool_a', 'tool', 'A', { x: 2200, y: 0 }),
      makeNode('tool_b', 'tool', 'B', { x: 4400, y: 0 }),
      makeNode('output', 'output', 'Output', { x: 6600, y: 0 }),
    ]
    const edges = makeLinearEdges(denseNodes.map((node) => node.id))

    const compactScene = buildThumbnailPreviewScene(denseNodes, edges, { width: 1200, height: 224 })
    const sparseScene = buildThumbnailPreviewScene(sparseNodes, edges, { width: 1200, height: 224 })

    expect(sparseScene.density).toBe(compactScene.density)
    expect(sparseScene.nodes.map((node) => node.position)).toEqual(compactScene.nodes.map((node) => node.position))
  })
})
