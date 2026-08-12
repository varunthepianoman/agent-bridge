import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WorkDetail } from './WorkDetail'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('WorkDetail Manual mode', () => {
  it('keeps direct Bridge dispatch first-class inside focused work', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 })))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><WorkDetail
      work={{ id: 'work-17', title: 'ARCI PR 17', objective: 'Validate reconnect', status: 'active', conversations: [] }}
      roles={[]} relationships={[]} allConversations={[]} loading={false} saving={false}
      onUpdate={vi.fn()} onAttach={vi.fn()} onDetach={vi.fn()} onCreateRole={vi.fn()}
      onCreateRelationship={vi.fn()} onDeleteRelationship={vi.fn()} onOpenConversation={vi.fn()}
    /></QueryClientProvider>)

    fireEvent.click(screen.getByRole('button', { name: 'Manual Bridge' }))
    expect(await screen.findByText('Coordinator bypass')).toBeInTheDocument()
    expect(screen.getByText('Attached to work-17 for visibility')).toBeInTheDocument()
  })

  it('expands durable role details', () => {
    const onOpenConversation = vi.fn()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><WorkDetail
      work={{ id: 'work-17', title: 'ARCI PR 17', status: 'active', conversations: [] }}
      roles={[{ id: 'role-1', role_type: 'worker', scope: 'work:work-17', charter: 'Remediate review feedback', authority_profile: 'read-write-local', autonomy_mode: 'delegate', checkpoint_version: 2, status: 'active', current_conversation_id: 'conv-1' }]}
      relationships={[]} allConversations={[]} loading={false} saving={false}
      onUpdate={vi.fn()} onAttach={vi.fn()} onDetach={vi.fn()} onCreateRole={vi.fn()}
      onCreateRelationship={vi.fn()} onDeleteRelationship={vi.fn()} onOpenConversation={onOpenConversation}
    /></QueryClientProvider>)

    const toggle = screen.getByRole('button', { name: /Remediate review feedback/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('role-1')).toBeInTheDocument()
    expect(screen.getByText('work:work-17')).toBeInTheDocument()
    expect(screen.getByText('conv-1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /conv-1/ }))
    expect(onOpenConversation).toHaveBeenCalledWith('conv-1')
  })
})
