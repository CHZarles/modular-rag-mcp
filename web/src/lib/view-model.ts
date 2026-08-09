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
