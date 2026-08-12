import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkPortfolio } from './WorkPortfolio'

describe('WorkPortfolio', () => {
  it('keeps work one level above conversation detail and creates focused work', () => {
    const onCreate = vi.fn()
    const onSelect = vi.fn()
    render(<WorkPortfolio
      items={[{ id: 'work-17', title: 'ARCI PR 17', objective: 'Validate reconnect', status: 'active', conversation_ids: ['conv-1'] }]}
      selectedId="work-17" loading={false} creating={false} onSelect={onSelect} onCreate={onCreate}
    />)

    fireEvent.click(screen.getByText('ARCI PR 17'))
    expect(onSelect).toHaveBeenCalledWith('work-17')

    fireEvent.click(screen.getByTitle('Create work item'))
    fireEvent.change(screen.getByLabelText('Work item title'), { target: { value: 'Agent Bridge M2' } })
    fireEvent.change(screen.getByLabelText('Work item objective'), { target: { value: 'Ship durable roles' } })
    fireEvent.click(screen.getByText('Create work item'))
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ title: 'Agent Bridge M2', objective: 'Ship durable roles', status: 'planned' }))
  })
})
