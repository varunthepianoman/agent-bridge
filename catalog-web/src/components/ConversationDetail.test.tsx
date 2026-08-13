import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConversationDetail } from './ConversationDetail'

afterEach(cleanup)

describe('ConversationDetail location handling', () => {
  it('opens a locally persisted Codex thread directly in the desktop app', () => {
    const onResume = vi.fn()
    render(<ConversationDetail
      conversation={{
        id: 'conv-local', provider: 'codex', title: 'PR remediation', provider_thread_id: 'thread/local',
        location: { node_id: 'runner-node', available: false },
        interactive_open: {
          desktop: { available: true, url: 'codex://threads/thread%2Flocal' },
          terminal: { available: true, command: 'codex resume thread/local' },
        },
      }}
      loading={false} saving={false} resuming={false} onUpdate={vi.fn()} onResume={onResume}
    />)

    expect(screen.getByRole('link', { name: 'Open in Codex desktop' })).toHaveAttribute(
      'href', 'codex://threads/thread%2Flocal',
    )
    expect(screen.queryByRole('button', { name: 'Open in Codex' })).not.toBeInTheDocument()
    expect(onResume).not.toHaveBeenCalled()
  })

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

  it('explains when the desktop thread is not local', () => {
    render(<ConversationDetail
      conversation={{
        id: 'conv-remote', provider: 'codex', provider_thread_id: 'thread-remote',
        location: { node_id: 'remote-node', node_name: 'Build host', available: true },
        interactive_open: {
          desktop: { available: false, reason: 'This Codex thread is not present in this machine\'s local history.' },
          terminal: { available: true, command: 'codex resume thread-remote' },
        },
      }}
      loading={false} saving={false} resuming={false} onUpdate={vi.fn()} onResume={vi.fn()}
    />)

    expect(screen.getByRole('status')).toHaveTextContent('Desktop chat unavailable on this machine')
    expect(screen.getByRole('status')).toHaveTextContent('not present in this machine\'s local history')
  })
})
