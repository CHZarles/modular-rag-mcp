import assert from 'node:assert/strict'
import test from 'node:test'

import { selectAvailableValue, summarizeRuntime } from '../src/lib/view-model.ts'

test('summarizeRuntime reports counts from API data', () => {
  const summary = summarizeRuntime(
    [{ enabled: true }, { enabled: false }, { enabled: true }],
    [{ indexed: true }, { indexed: false }],
  )

  assert.deepEqual(summary, {
    enabledComponents: 2,
    totalComponents: 3,
    indexedCollections: 1,
    totalCollections: 2,
  })
})

test('selectAvailableValue replaces an option that is no longer available', () => {
  assert.equal(selectAvailableValue('removed.json', ['current.json']), 'current.json')
  assert.equal(selectAvailableValue('current.json', ['current.json']), 'current.json')
  assert.equal(selectAvailableValue('removed.json', []), '')
})
