import assert from 'node:assert/strict'
import test from 'node:test'

import { queryImageDataUrl, selectAvailableValue, summarizeRuntime } from '../src/lib/view-model.ts'

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

test('queryImageDataUrl resolves an MCP-compatible image content index', () => {
  const content = [
    { type: 'text', text: 'results' },
    { type: 'image', data: 'aW1hZ2U=', mimeType: 'image/png' },
  ]

  assert.equal(queryImageDataUrl(content, 1), 'data:image/png;base64,aW1hZ2U=')
  assert.equal(queryImageDataUrl(content, 0), null)
  assert.equal(queryImageDataUrl(content, 9), null)
})
