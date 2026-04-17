import { describe, expect, it } from 'vitest'

import type { Edge, Node } from '@xyflow/react'

import type { ContainerBodyEdge, ContainerBodyNode } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import {
  autoLayoutContainerBodyNodes,
  autoLayoutWorkflowWithSubflows,
  normalizeContainerPreviewBodyNodes,
} from './autoLayout'
import { estimateContainerNodeSizeFromConfig } from './workflowGeometry'

function makeMainNode(node: Partial<Node<WfNodeData>> & { id: string; nodeType: WfNodeData['nodeType'] }): Node<WfNodeData> {
  return {
    id: node.id,
    type: node.nodeType,
    position: node.position ?? { x: 0, y: 0 },
    data: {
      nodeType: node.nodeType,
      label: node.data?.label ?? node.nodeType,
      config: node.data?.config ?? null,
    },
  } as Node<WfNodeData>
}

function makeBodyNode(node: Partial<ContainerBodyNode> & { nodeId: string; nodeType: ContainerBodyNode['nodeType'] }): ContainerBodyNode {
  return {
    nodeId: node.nodeId,
    nodeType: node.nodeType,
    label: node.label ?? node.nodeType,
    positionX: node.positionX,
    positionY: node.positionY,
    config: node.config ?? null,
  }
}

function makeBodyEdge(edge: Partial<ContainerBodyEdge> & { edgeId: string; sourceNodeId: string; targetNodeId: string }): ContainerBodyEdge {
  return {
    edgeId: edge.edgeId,
    sourceNodeId: edge.sourceNodeId,
    targetNodeId: edge.targetNodeId,
    sourceHandle: edge.sourceHandle ?? 'output',
    targetHandle: edge.targetHandle ?? 'input',
  }
}

describe('autoLayout workflow nesting', () => {
  it('lays out container bodies before sizing and placing the outer graph', () => {
    const bodyNodes = [
      makeBodyNode({ nodeId: 'start', nodeType: 'start', positionX: 40, positionY: 72 }),
      makeBodyNode({ nodeId: 'tool_target', nodeType: 'tool', positionX: 1040, positionY: 72 }),
      makeBodyNode({ nodeId: 'code_candidate', nodeType: 'code_executor', positionX: 2040, positionY: 72 }),
    ]
    const bodyEdges = [
      makeBodyEdge({ edgeId: 'e1', sourceNodeId: 'start', targetNodeId: 'tool_target' }),
      makeBodyEdge({ edgeId: 'e2', sourceNodeId: 'tool_target', targetNodeId: 'code_candidate' }),
    ]
    const originalSize = estimateContainerNodeSizeFromConfig({ bodyNodes, bodyEdges })

    const nodes = [
      makeMainNode({ id: 'start', nodeType: 'start', position: { x: 80, y: 320 } }),
      makeMainNode({
        id: 'iter_relation_details',
        nodeType: 'iteration',
        position: { x: 480, y: 320 },
        data: {
          nodeType: 'iteration',
          label: 'Iteration',
          config: { bodyNodes, bodyEdges },
        },
      }),
      makeMainNode({ id: 'output_final', nodeType: 'output', position: { x: 880, y: 320 } }),
    ]
    const edges: Edge[] = [
      { id: 'main_e1', source: 'start', target: 'iter_relation_details' },
      { id: 'main_e2', source: 'iter_relation_details', target: 'output_final' },
    ]

    const laidOut = autoLayoutWorkflowWithSubflows(nodes, edges)
    const containerNode = laidOut.find((node) => node.id === 'iter_relation_details')
    const outputNode = laidOut.find((node) => node.id === 'output_final')

    expect(containerNode).toBeDefined()
    expect(outputNode).toBeDefined()

    const nextConfig = (containerNode?.data.config ?? {}) as Record<string, unknown>
    const nextSize = estimateContainerNodeSizeFromConfig(nextConfig)
    const nextBodyNodes = (nextConfig.bodyNodes ?? []) as ContainerBodyNode[]
    const minX = Math.min(...nextBodyNodes.map((node) => Number(node.positionX ?? 0)))
    const minY = Math.min(...nextBodyNodes.map((node) => Number(node.positionY ?? 0)))
    const originalSpan = Math.max(...bodyNodes.map((node) => Number(node.positionX ?? 0))) - Math.min(...bodyNodes.map((node) => Number(node.positionX ?? 0)))
    const nextSpan = Math.max(...nextBodyNodes.map((node) => Number(node.positionX ?? 0))) - Math.min(...nextBodyNodes.map((node) => Number(node.positionX ?? 0)))

    expect(nextSize.width).toBeLessThanOrEqual(1120)
    expect(nextSpan).toBeLessThan(originalSpan)
    expect(nextSize.width).toBeLessThan(originalSize.width)
    expect(minX).toBe(40)
    expect(minY).toBe(56)
    expect((outputNode?.position.x ?? 0) - (containerNode?.position.x ?? 0)).toBeLessThan(originalSpan)
  })

  it('normalizes body node positions into a stable subflow canvas origin', () => {
    const laidOut = autoLayoutContainerBodyNodes(
      [
        makeBodyNode({ nodeId: 'start', nodeType: 'start', positionX: 420, positionY: 160 }),
        makeBodyNode({ nodeId: 'tool_1', nodeType: 'tool', positionX: 60, positionY: 480 }),
        makeBodyNode({ nodeId: 'human_gate', nodeType: 'human_in_loop', positionX: 860, positionY: 20 }),
      ],
      [
        makeBodyEdge({ edgeId: 'be1', sourceNodeId: 'start', targetNodeId: 'tool_1' }),
        makeBodyEdge({ edgeId: 'be2', sourceNodeId: 'tool_1', targetNodeId: 'human_gate' }),
      ],
    )

    const xs = laidOut.map((node) => Number(node.positionX ?? 0))
    const ys = laidOut.map((node) => Number(node.positionY ?? 0))

    expect(Math.min(...xs)).toBe(40)
    expect(Math.min(...ys)).toBe(56)
    expect(laidOut[0]?.positionX).toBeLessThan(laidOut[1]?.positionX ?? 0)
    expect(laidOut[1]?.positionX).toBeLessThan(laidOut[2]?.positionX ?? 0)
  })

  it('keeps auto-layout stable across repeated runs even if container measured size is stale', () => {
    const bodyNodes = [
      makeBodyNode({ nodeId: 'body_start', nodeType: 'start', positionX: 40, positionY: 56 }),
      makeBodyNode({ nodeId: 'body_tool', nodeType: 'tool', positionX: 360, positionY: 56 }),
      makeBodyNode({ nodeId: 'body_human', nodeType: 'human_in_loop', positionX: 680, positionY: 56 }),
    ]
    const bodyEdges = [
      makeBodyEdge({ edgeId: 'be1', sourceNodeId: 'body_start', targetNodeId: 'body_tool' }),
      makeBodyEdge({ edgeId: 'be2', sourceNodeId: 'body_tool', targetNodeId: 'body_human' }),
    ]

    const nodes = [
      makeMainNode({ id: 'start', nodeType: 'start', position: { x: 80, y: 320 } }),
      {
        ...makeMainNode({
          id: 'container',
          nodeType: 'iteration',
          position: { x: 420, y: 320 },
          data: {
            nodeType: 'iteration',
            label: 'Iteration',
            config: { bodyNodes, bodyEdges },
          },
        }),
        measured: { width: 1500, height: 720 },
      } as Node<WfNodeData>,
      makeMainNode({ id: 'final', nodeType: 'output', position: { x: 820, y: 320 } }),
    ]
    const edges: Edge[] = [
      { id: 'e1', source: 'start', target: 'container' },
      { id: 'e2', source: 'container', target: 'final' },
    ]

    const first = autoLayoutWorkflowWithSubflows(nodes, edges)
    const second = autoLayoutWorkflowWithSubflows(first, edges)

    expect(second).toEqual(first)
  })

  it('compacts sparse saved subflow coordinates for preview rendering', () => {
    const bodyNodes = [
      makeBodyNode({ nodeId: 'start', nodeType: 'start', positionX: 40, positionY: 56 }),
      makeBodyNode({ nodeId: 'tool_1', nodeType: 'tool', positionX: 1040, positionY: 56 }),
      makeBodyNode({ nodeId: 'tool_2', nodeType: 'tool', positionX: 2040, positionY: 56 }),
      makeBodyNode({ nodeId: 'tool_3', nodeType: 'tool', positionX: 3040, positionY: 56 }),
    ]
    const bodyEdges = [
      makeBodyEdge({ edgeId: 'be1', sourceNodeId: 'start', targetNodeId: 'tool_1' }),
      makeBodyEdge({ edgeId: 'be2', sourceNodeId: 'tool_1', targetNodeId: 'tool_2' }),
      makeBodyEdge({ edgeId: 'be3', sourceNodeId: 'tool_2', targetNodeId: 'tool_3' }),
    ]

    const previewNodes = normalizeContainerPreviewBodyNodes(bodyNodes, bodyEdges)
    const originalSpan = Math.max(...bodyNodes.map((node) => Number(node.positionX ?? 0))) - Math.min(...bodyNodes.map((node) => Number(node.positionX ?? 0)))
    const previewSpan = Math.max(...previewNodes.map((node) => Number(node.positionX ?? 0))) - Math.min(...previewNodes.map((node) => Number(node.positionX ?? 0)))

    expect(previewSpan).toBeLessThan(originalSpan / 2)
    expect(Math.min(...previewNodes.map((node) => Number(node.positionX ?? 0)))).toBe(40)
    expect(Math.min(...previewNodes.map((node) => Number(node.positionY ?? 0)))).toBe(56)
  })
})
