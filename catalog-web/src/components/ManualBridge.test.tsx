import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ManualBridge } from './ManualBridge'

function renderBridge(workId = 'work-17') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><ManualBridge workId={workId} /></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('ManualBridge', () => {
  it('submits a guided coordinator-bypass request without client-controlled identities', async () => {
    const requests: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, init })
      if (!init?.method) return new Response(JSON.stringify({ items: [] }), { status: 200 })
      return new Response(JSON.stringify({ execution: { execution_id: 'exec-1', status: 'queued' }, message: { message_id: 'msg-1' } }), { status: 201 })
    }))
    renderBridge()

    fireEvent.change(screen.getByLabelText('Destination type'), { target: { value: 'capability' } })
    fireEvent.change(screen.getByLabelText('Destination identity'), { target: { value: 'abb.robot-test' } })
    fireEvent.change(screen.getByLabelText('Instruction'), { target: { value: 'Run reconnect validation' } })
    fireEvent.change(screen.getByLabelText('Working directory'), { target: { value: '/workspace/robot' } })
    fireEvent.change(screen.getByLabelText('Structured body'), { target: { value: '{"suite":"rws"}' } })
    fireEvent.change(screen.getByLabelText('Reply identity'), { target: { value: 'manual-ui' } })
    fireEvent.click(screen.getByText('Submit to Bridge'))

    await screen.findByText('Accepted by Bridge')
    const post = requests.find((entry) => entry.init?.method === 'POST')
    expect(post?.url).toContain('/bridge/requests')
    const body = JSON.parse(String(post?.init?.body))
    expect(body.request).toEqual(expect.objectContaining({
      operation: 'new_execution', instruction: 'Run reconnect validation',
      target: { kind: 'capability', id: 'abb.robot-test' }, work_id: 'work-17',
      cwd: '/workspace/robot',
      parameters: { suite: 'rws' },
    }))
    expect(body.envelope).toEqual({ reply_to: { kind: 'endpoint', id: 'manual-ui' } })
    expect(body.request).not.toHaveProperty('execution_id')
    expect(body).not.toHaveProperty('sender')
  })

  it('validates namespaced extensions before dispatch', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    renderBridge()
    fireEvent.change(screen.getByLabelText('Destination identity'), { target: { value: 'runner' } })
    fireEvent.change(screen.getByLabelText('Instruction'), { target: { value: 'Do bounded work' } })
    fireEvent.click(screen.getByText('Advanced envelope'))
    fireEvent.change(screen.getByLabelText('Namespaced extensions'), { target: { value: '{"unsafe":true}' } })
    fireEvent.click(screen.getByText('Submit to Bridge'))

    expect(await screen.findByRole('alert')).toHaveTextContent('must be namespaced')
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
  })

  it('addresses a runner separately from the Codex conversation being resumed', async () => {
    const requests: Array<{ url: string, init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, init })
      if (!init?.method) return new Response(JSON.stringify({ items: [] }), { status: 200 })
      return new Response(JSON.stringify({ execution: { execution_id: 'exec-2', status: 'queued' } }), { status: 201 })
    }))
    renderBridge()

    fireEvent.change(screen.getByLabelText('Execution operation'), { target: { value: 'resume_conversation' } })
    fireEvent.change(screen.getByLabelText('Destination type'), { target: { value: 'node' } })
    fireEvent.change(screen.getByLabelText('Destination identity'), { target: { value: 'local-codex' } })
    fireEvent.change(screen.getByLabelText('Codex conversation ID'), { target: { value: 'thread-1344' } })
    fireEvent.change(screen.getByLabelText('Working directory'), { target: { value: '/workspace/pr-1344' } })
    fireEvent.change(screen.getByLabelText('Instruction'), { target: { value: 'Explain the new review threads' } })
    fireEvent.click(screen.getByText('Submit to Bridge'))

    await screen.findByText('Accepted by Bridge')
    const post = requests.find((entry) => entry.init?.method === 'POST')
    const body = JSON.parse(String(post?.init?.body))
    expect(body.request).toEqual(expect.objectContaining({
      operation: 'resume_conversation',
      target: { kind: 'node', id: 'local-codex' },
      conversation_id: 'thread-1344',
      cwd: '/workspace/pr-1344',
    }))
  })
})
