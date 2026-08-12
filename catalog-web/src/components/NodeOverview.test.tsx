import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NodeOverview } from './NodeOverview'

describe('NodeOverview', () => {
  it('keeps reachability separate from durable conversation state', () => {
    render(<NodeOverview loading={false} onRetry={vi.fn()} nodes={[
      {
        id: 'node-win', name: 'Windows workstation', platform: 'windows', reachability: 'offline',
        reachable: false, last_seen_at: '2026-08-11T12:00:00Z', capabilities: [],
        environments: [{ id: 'wsl-arci', name: 'ARCI WSL', kind: 'wsl', available: false }],
      },
    ]} />)

    expect(screen.getByText('Windows workstation')).toBeInTheDocument()
    expect(screen.getByText('ARCI WSL')).toBeInTheDocument()
    expect(screen.getByText('wsl · unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Conversation history remains available/)).toBeInTheDocument()
  })
})
