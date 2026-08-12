import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { Conversation, CoordinatorRole, WorkItem, WorkRelationship } from '../types'

interface Props {
  work: WorkItem
  conversations: Conversation[]
  roles: CoordinatorRole[]
  relationships: WorkRelationship[]
}

export function WorkGraph({ work, conversations, roles, relationships }: Props) {
  const entities = new Map<string, Node>()
  entities.set(work.id, makeNode(work.id, work.title, 'Work item', 0, 0, 'work'))
  roles.forEach((role, index) => entities.set(role.id, makeNode(
    role.id,
    role.charter || role.role_type.replaceAll('_', ' '),
    role.role_type.replaceAll('_', ' '),
    280,
    index * 110,
    'role',
  )))
  conversations.forEach((conversation, index) => entities.set(conversation.id, makeNode(
    conversation.id,
    conversation.catalog_title || conversation.title || 'Untitled conversation',
    `${conversation.provider} conversation`,
    570,
    index * 100,
    'conversation',
  )))

  // Graph and list intentionally consume the exact same relationship collection.
  const visible = relationships.filter((relationship) => entities.has(relationship.source_id) && entities.has(relationship.target_id))
  const edges: Edge[] = visible.map((relationship) => ({
    id: relationship.id,
    source: relationship.source_id,
    target: relationship.target_id,
    label: relationship.label || relationship.kind.replaceAll('_', ' '),
    markerEnd: { type: MarkerType.ArrowClosed, color: '#76c7b7' },
    style: { stroke: '#527b76' },
    labelStyle: { fill: '#91aaa6', fontSize: 10 },
  }))

  return (
    <div className="work-graph" aria-label="Work relationship graph">
      <ReactFlow nodes={[...entities.values()]} edges={edges} fitView minZoom={0.4} maxZoom={1.5} nodesDraggable={false} nodesConnectable={false}>
        <Background color="#243239" gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

function makeNode(id: string, label: string, subtitle: string, x: number, y: number, kind: string): Node {
  return {
    id,
    position: { x, y },
    data: { label: <div className="graph-node"><span>{subtitle}</span><strong title={label}>{label}</strong></div> },
    className: `graph-node-shell ${kind}`,
  }
}
