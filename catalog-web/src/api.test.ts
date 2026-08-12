import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRelationship, createRole, getConversation, listConversations, listNodes, listRoles, listWorkItems, resumeConversation } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('Catalog API client', () => {
  it('normalizes a bare conversation array and sends filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { conversation_id: 'conv-1', provider: 'codex', title: 'First', source: 'vscode' },
    ]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await listConversations({ query: 'robot test', status: 'active', source: 'vscode', view: 'pinned' })

    expect(result.items[0]).toEqual(expect.objectContaining({ id: 'conv-1', provider: 'codex', source_kind: 'vscode' }))
    const requestUrl = new URL(fetchMock.mock.calls[0][0], 'http://catalog.test')
    expect(requestUrl.pathname).toBe('/api/v1/conversations')
    expect(Object.fromEntries(requestUrl.searchParams)).toEqual(expect.objectContaining({
      q: 'robot test',
      status: 'active',
      source: 'vscode',
      pinned: 'true',
      archived: 'false',
    }))
  })

  it('requests an explicit native launch when resuming', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ launched: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await resumeConversation('conversation/one')

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/actions/resume', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ conversation_id: 'conversation/one', launch: true }),
    }))
  })

  it('preserves explicit owning-environment unavailability', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      conversation_id: 'conv-remote', provider: 'codex', node_id: 'windows-dev',
      environment_id: 'host', node_reachable: false, environment_available: false,
      location_available: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const conversation = await getConversation('conv-remote')

    expect(conversation.location).toEqual(expect.objectContaining({
      node_id: 'windows-dev', environment: 'host', available: false,
    }))
  })

  it('normalizes the node reachability projection', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{
      node_id: 'node-win', display_name: 'Windows workstation', platform: 'windows',
      reachable: false, last_seen_at: '2026-08-11T12:00:00Z',
      environments: [{ environment_id: 'wsl-arci', display_name: 'ARCI WSL', kind: 'wsl', available: false }],
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const nodes = await listNodes()

    expect(nodes[0]).toEqual(expect.objectContaining({
      id: 'node-win', name: 'Windows workstation', reachability: 'offline', reachable: false,
    }))
    expect(nodes[0].environments[0]).toEqual(expect.objectContaining({ id: 'wsl-arci', name: 'ARCI WSL', available: false }))
  })

  it('scopes durable roles to a selected work item', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{
      id: 'role-1', role_type: 'work_coordinator', charter: 'Coordinate PR 17', checkpoint_version: 0, status: 'planned',
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const roles = await listRoles('work/pr-17')

    expect(roles).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/roles?work_item_id=work%2Fpr-17', expect.any(Object))
  })

  it('normalizes protocol work identity for the portfolio', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{
      work_id: 'work-17', title: 'ARCI PR 17', objective: 'Reconnect', status: 'active', repository_id: 'arci-v2',
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const work = await listWorkItems()

    expect(work[0]).toEqual(expect.objectContaining({ id: 'work-17', repository: 'arci-v2' }))
  })

  it('maps a durable role to the backend work scope', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      role_id: 'role-17', role_type: 'work_coordinator', scope: 'work:work-17', charter: 'Coordinate PR 17',
      authority_profile: 'delegate-bounded', checkpoint_version: 0, status: 'draft',
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const role = await createRole({
      role_type: 'work_coordinator', work_item_id: 'work-17', charter: 'Coordinate PR 17',
      authority_profile: 'delegate-bounded', status: 'planned',
    })

    expect(role).toEqual(expect.objectContaining({ id: 'role-17', work_item_id: 'work-17', status: 'draft' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/roles', expect.objectContaining({
      body: JSON.stringify({ role_type: 'work_coordinator', charter: 'Coordinate PR 17', authority_profile: 'delegate-bounded', status: 'draft', scope: 'work:work-17' }),
    }))
  })

  it('posts the shared relationship model used by list and graph', async () => {
    const relationship = {
      work_item_id: 'work-1', source_type: 'role' as const, source_id: 'role-1',
      target_type: 'conversation' as const, target_id: 'conv-1', kind: 'coordinates',
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      relationship_id: 'rel-1', source: { kind: 'role', id: 'role-1' },
      target: { kind: 'conversation', id: 'conv-1' }, type: 'coordinates', metadata: { work_item_id: 'work-1' },
    }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await createRelationship(relationship)

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/relationships', expect.objectContaining({
      method: 'POST', body: JSON.stringify({
        source: { kind: 'role', id: 'role-1' }, target: { kind: 'conversation', id: 'conv-1' },
        type: 'coordinates', metadata: { work_item_id: 'work-1' },
      }),
    }))
  })
})
