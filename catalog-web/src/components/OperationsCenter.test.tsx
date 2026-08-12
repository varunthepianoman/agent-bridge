import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { getOperationsSnapshot } from '../api'
import type { OperationsSnapshot } from '../types'
import { OperationsCenter } from './OperationsCenter'

vi.mock('../api', () => ({ getOperationsSnapshot: vi.fn() }))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class {
    observe() { /* layout is not exercised in this test */ }
    unobserve() { /* layout is not exercised in this test */ }
    disconnect() { /* layout is not exercised in this test */ }
  })
})

const snapshot: OperationsSnapshot = {
  summary: {
    status: 'degraded', broker_status: 'connected', background_status: 'healthy',
    counts: { nodes: 2, unreachable_nodes: 1, roles: 1, executions: 2, pending_requests: 1, attention_required: 0, unresolved_dead_letters: 1, consumer_pending: 3 },
    advisories: [{ severity: 'error', code: 'unresolved_dead_letters', message: 'dead letters require attention' }],
    observed_at: '2026-08-11T17:00:00Z',
  },
  nodes: [{ id: 'robot', name: 'Robot host', reachability: 'offline', reachable: false, environments: [], capabilities: ['robot-test'] }],
  roles: [{ id: 'role-pr17', role_type: 'work_coordinator', charter: 'Coordinate PR 17', checkpoint_version: 8, status: 'active' }],
  pending: [{ execution_id: 'exec-test', status: 'queued', instruction: 'Run simulator E2E', target: { kind: 'capability', id: 'robot-test' } }],
  leases: [{ lease_type: 'role', resource_id: 'role-pr17', holder_id: 'coordinator-a', fencing_token: 4, acquired_at: '2026-08-11T16:00:00Z', expires_at: '2026-08-11T18:00:00Z', active: true }],
  retries: [],
  deadLetters: [{ dead_letter_id: 'dead-1', message_id: 'msg-1', stream: 'BRIDGE_WORK_V1', consumer: 'robot-runner', reason: 'attempts_exhausted', attempts: 3, dead_lettered_at: '2026-08-11T16:30:00Z' }],
  artifacts: [{ name: 'results.json', uri: 'git://repo/results.json', execution_id: 'exec-old' }],
  executions: [{ execution_id: 'exec-test', status: 'queued', instruction: 'Run simulator E2E', attempt_count: 0 }],
  broker: { status: 'connected', connected: true, streams: [{ name: 'BRIDGE_WORK_V1', messages: 4, consumer_count: 2 }], consumers: [{ stream: 'BRIDGE_WORK_V1', consumer: 'robot-runner', pending_count: 3 }], advisories: [] },
  messages: [{ message_id: 'msg-1', subject: 'bridge.v1.capability.robot-test', message_type: 'request', source_kind: 'role', source_id: 'role-pr17', destination_kind: 'capability', destination_id: 'robot-test', state: 'published', stream: 'BRIDGE_WORK_V1', last_observed_at: '2026-08-11T16:00:00Z' }],
  deliveries: [{ delivery_id: 'delivery-1', message_id: 'msg-1', consumer: 'robot-runner', state: 'delivered', redelivery_count: 2, last_observed_at: '2026-08-11T16:30:00Z' }],
  background: { status: 'healthy', tasks: [{ name: 'result-projection', state: 'running', critical: true, started_at: '2026-08-11T15:00:00Z' }] },
  relationships: [{ id: 'rel-1', source_type: 'work', source_id: 'work-pr17', target_type: 'role', target_id: 'role-pr17', kind: 'contains' }],
}

function renderCenter() {
  vi.mocked(getOperationsSnapshot).mockResolvedValue(snapshot)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><OperationsCenter /></QueryClientProvider>)
}

afterEach(cleanup)

describe('OperationsCenter', () => {
  it('leads with procedural state and keeps raw broker details separate', async () => {
    renderCenter()
    expect(await screen.findByText('Current procedure')).toBeInTheDocument()
    expect(screen.getAllByText('Run simulator E2E').length).toBeGreaterThan(0)
    expect(screen.getByText('Coordinate PR 17')).toBeInTheDocument()
    expect(screen.getByText('dead letters require attention')).toBeInTheDocument()
    expect(screen.queryByText('BRIDGE_WORK_V1')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('diagnostics'))
    expect(screen.getByText('Raw diagnostics')).toBeInTheDocument()
    expect(screen.getByText('BRIDGE_WORK_V1')).toBeInTheDocument()
    expect(screen.getByText(/Normal work should be managed/)).toBeInTheDocument()
  })

  it('shows declared relationships and observed routes in the same topology list', async () => {
    renderCenter()
    await screen.findByText('Current procedure')
    fireEvent.click(screen.getByText('topology'))
    fireEvent.click(screen.getByText('List'))
    expect(screen.getByText('declared relationship')).toBeInTheDocument()
    expect(screen.getByText('observed route')).toBeInTheDocument()
    expect(screen.getByText('request')).toBeInTheDocument()
  })
})
