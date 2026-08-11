import assert from 'node:assert/strict'
import test from 'node:test'

import { formatElapsedSeconds } from '../src/lib/format.ts'
import { grepPatternLength, queryImageDataUrl, selectAvailableValue, summarizeRuntime, validateUploadCandidate } from '../src/lib/view-model.ts'

test('formatElapsedSeconds keeps benchmark runtime readable', () => {
  assert.equal(formatElapsedSeconds(0), '0 秒')
  assert.equal(formatElapsedSeconds(65), '1 分 5 秒')
})

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

test('grepPatternLength counts Unicode code points without trimming', () => {
  assert.equal(grepPatternLength(' 😀 '), 3)
  assert.equal(grepPatternLength('😀😀😀'), 3)
  assert.equal(grepPatternLength('   '), 3)
  assert.equal(grepPatternLength('😀'.repeat(4001)), 4001)
})

test('validateUploadCandidate follows server-provided extensions and size limit', () => {
  const extensions = ['.pdf', '.docx', '.png']

  assert.equal(validateUploadCandidate('ARCHITECTURE.PNG', 1024, extensions, 2048), null)
  assert.equal(validateUploadCandidate('notes.txt', 1024, extensions, 2048), 'unsupported_file_type')
  assert.equal(validateUploadCandidate('large.pdf', 4096, extensions, 2048), 'file_too_large')
})
