import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ConversationDetail } from './ConversationDetail'

describe('ConversationDetail location handling', () => {
  it('refuses a silent fallback when the original environment is unavailable', () => {
    render(<ConversationDetail
      conversation={{
        id: 'conv-1', provider: 'codex', title: 'Robot reconnect', provider_thread_id: 'thread-1',
        location: { node_id: 'node-win', node_name: 'Windows workstation', environment: 'wsl', cwd: '/work/arci', available: false },
      }}
      loading={false} saving={false} resuming={false} onUpdate={vi.fn()} onResume={vi.fn()}
    />)

    expect(screen.getByRole('alert')).toHaveTextContent('Original environment unavailable')
    expect(screen.getByRole('button', { name: 'Open in Codex' })).toBeDisabled()
    expect(screen.getByText(/codex resume thread-1/)).toBeInTheDocument()
  })
})
