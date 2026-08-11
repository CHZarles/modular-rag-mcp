export function summarizeRuntime(
  components: ReadonlyArray<{ enabled: boolean }>,
  collections: ReadonlyArray<{ indexed: boolean }>,
) {
  return {
    enabledComponents: components.filter((component) => component.enabled).length,
    totalComponents: components.length,
    indexedCollections: collections.filter((collection) => collection.indexed).length,
    totalCollections: collections.length,
  }
}

export function selectAvailableValue(current: string, options: readonly string[]): string {
  return options.includes(current) ? current : options[0] ?? ''
}

export function queryImageDataUrl(
  content: ReadonlyArray<{ type: string; data?: unknown; mimeType?: unknown }>,
  index: number,
): string | null {
  const block = content[index]
  if (block?.type !== 'image' || typeof block.data !== 'string' || !block.data) return null
  if (typeof block.mimeType !== 'string' || !block.mimeType.startsWith('image/')) return null
  return `data:${block.mimeType};base64,${block.data}`
}

export function grepPatternLength(value: string): number {
  return Array.from(value).length
}
