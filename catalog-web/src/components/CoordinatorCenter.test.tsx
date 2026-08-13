import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CoordinatorCenter } from './CoordinatorCenter'

const roles = [{
  role_id: 'role-work-17', role_type: 'work_coordinator', scope: 'work:work-17',
  charter: 'Coordinate PR 17', authority_profile: 'delegate-bounded', autonomy_mode: 'delegate',
  checkpoint_version: 4, status: 'active',
}]
const intake = {
  request_id: 'req-review', objective: 'Review reconnect plan', mode: 'advise',
  status: 'awaiting_approval', proposed_actions: ['Run planner audit'],
  proposed_topology: { coordinator: 'role-work-17' }, attention_required: 'Approve planner audit',
  approval_required: true, executed: false,
}

function setupFetch() {
  const calls: Array<{ url: string, init?: RequestInit }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    if (init?.method === 'POST' && url.includes('/decision')) return response({ ...intake, status: 'approved', approval_required: false })
    if (init?.method === 'POST' && url.endsWith('/coordinator/intake')) return response({ ...intake, request_id: 'req-new', objective: 'Implement reconnect', status: 'submitted', approval_required: false })
    if (url.endsWith('/roles')) return response({ items: roles })
    if (url.includes('/rollups')) return response({ items: [{ parent_role_id: 'role-work-17', child_role_id: 'role-worker', incorporated_checkpoint_version: 2, current_checkpoint_version: 3, stale: true }] })
    if (url.includes('/reports')) return response({ items: [{ report_id: 'report-1', reporting_role_id: 'role-worker', recipient_role_id: 'role-work-17', checkpoint_version: 3, status: 'active', summary: 'Reconnect test is running', decisions: [], attention_required: 'Need robot access' }] })
    if (url.includes('/activations')) return response({ items: [] })
    if (url.includes('/coordinator/intake')) return response({ items: [intake] })
    return response({ items: [] })
  }))
  return calls
}

function renderCenter(onOpenManual = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><CoordinatorCenter workItems={[{ id: 'work-17', title: 'ARCI PR 17', status: 'active' }]} onOpenManual={onOpenManual} /></QueryClientProvider>)
  return onOpenManual
}

function renderCenterWithImplementationGate() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><CoordinatorCenter workItems={[{
    id: 'work-17', title: 'ARCI PR 17', status: 'active',
    extensions: { 'agent_bridge.convergence': { state: { status: 'awaiting_user_implementation_approval' } } },
  }]} onOpenManual={vi.fn()} /></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('CoordinatorCenter', () => {
  it('routes Manual mode to the direct Bridge without coordinator activity', async () => {
    const calls = setupFetch()
    const openManual = renderCenter()
    expect((await screen.findAllByText('Review reconnect plan')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByText('manual'))
    fireEvent.click(screen.getByText('Continue manually'))
    expect(openManual).toHaveBeenCalledOnce()
    expect(calls.filter((call) => call.init?.method === 'POST')).toHaveLength(0)
  })

  it('targets a work coordinator, submits bounded authority, surfaces stale reports, and approves', async () => {
    const calls = setupFetch()
    renderCenter()
    expect((await screen.findAllByText('Coordinate PR 17')).length).toBeGreaterThan(0)
    fireEvent.change(screen.getByLabelText('Target coordinator'), { target: { value: 'role-work-17' } })
    expect(await screen.findByText('stale rollup')).toBeInTheDocument()
    expect(screen.getByText('Attention: Need robot access')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Coordinator objective'), { target: { value: 'Implement reconnect' } })
    fireEvent.change(screen.getByLabelText('Coordinator work scope'), { target: { value: 'work-17' } })
    fireEvent.click(screen.getByText('Send to role'))
    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST' && call.url.endsWith('/coordinator/intake'))).toBe(true))
    const submitted = calls.find((call) => call.init?.method === 'POST' && call.url.endsWith('/coordinator/intake'))
    expect(JSON.parse(String(submitted?.init?.body))).toEqual(expect.objectContaining({
      objective: 'Implement reconnect', mode: 'delegate', work_id: 'work-17', target_role_id: 'role-work-17',
      authority: expect.objectContaining({ max_parallel_executions: 1, max_attempts: 3, may_expand_scope: false }),
    }))

    fireEvent.click(screen.getByText('Approve'))
    await waitFor(() => expect(calls.some((call) => call.url.includes('/req-review/decision'))).toBe(true))
  })

  it('requires explicit budget and deadline before autonomous execution', async () => {
    const calls = setupFetch()
    renderCenter()
    await screen.findByLabelText('Coordinator objective')
    fireEvent.click(screen.getByText('autonomous'))
    fireEvent.change(screen.getByLabelText('Coordinator objective'), { target: { value: 'Explore and adapt' } })
    fireEvent.click(screen.getByText('Submit to portfolio'))
    expect(await screen.findByRole('alert')).toHaveTextContent('explicit deadline and token or cost budget')
    expect(calls.filter((call) => call.init?.method === 'POST')).toHaveLength(0)
  })

  it('includes convergence implementation gates in the attention queue', async () => {
    const calls = setupFetch()
    renderCenterWithImplementationGate()
    const approve = await screen.findByRole('button', { name: 'Approve implementation' })
    expect(screen.getByText('awaiting implementation approval')).toBeInTheDocument()
    fireEvent.click(approve)
    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST' && call.url.includes('/convergence/approve-implementation'))).toBe(true))
  })
})

function response(value: unknown) { return Promise.resolve(new Response(JSON.stringify(value), { status: 200 })) }
