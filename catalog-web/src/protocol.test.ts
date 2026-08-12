import Ajv2020 from 'ajv/dist/2020'
import { describe, expect, it } from 'vitest'

import envelope from '../../schemas/examples/bridge-envelope.json'
import schema from '../../schemas/agent-bridge-v1.schema.json'
import type { AgentBridgeProtocolV1 } from './generated/protocol'

describe('shared protocol contract', () => {
  it('validates the same envelope fixture used by Python', () => {
    const typedEnvelope: AgentBridgeProtocolV1 = envelope as AgentBridgeProtocolV1
    const validate = new Ajv2020({ strict: false, validateFormats: false }).compile(schema)
    expect(validate(typedEnvelope), JSON.stringify(validate.errors)).toBe(true)
  })
})
