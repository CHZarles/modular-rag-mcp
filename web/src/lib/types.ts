export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[]
export type JsonObject = { [key: string]: JsonValue }

export interface ComponentDetail {
  label: string
  value: string
}

export interface ComponentSummary {
  code: string
  label: string
  provider: string
  model: string | null
  enabled: boolean
  details: ComponentDetail[]
}

export interface CollectionSummary {
  name: string
  indexed: boolean
}

export interface Stats {
  name: string
  document_count: number
  chunk_count: number
  image_count: number
}

export interface Overview {
  components: ComponentSummary[]
  collections: CollectionSummary[]
  stats: Stats
}

export interface ComponentResponse {
  code: string
  values: Record<string, JsonValue>
  provider_options: string[]
}

export interface CollectionResponse {
  name: string
  collections: string[]
}

export interface DocumentSummary {
  doc_id: string
  source_path: string
  title: string | null
  summary: string | null
  tags: string[]
  metadata: Record<string, JsonValue>
}

export interface DocumentListResponse {
  collection: string | null
  documents: DocumentSummary[]
  stats: Stats
}

export interface ChunkDetail {
  id?: string
  chunk_id?: string
  text?: string
  content?: string
  metadata?: Record<string, JsonValue>
  source_ref?: string
  chunk_index?: number
  score?: number
  [key: string]: JsonValue | undefined
}

export interface DocumentDetail {
  document: DocumentSummary
  chunks: ChunkDetail[]
  images: JsonValue[]
}

export interface TraceStage {
  name: string
  timestamp: string
  elapsed_ms: number | null
  method: string
  provider: string
  details: Record<string, JsonValue>
  data: Record<string, JsonValue>
}

export interface TraceRecord {
  trace_id: string
  trace_type: string
  started_at: string
  finished_at: string | null
  total_elapsed_ms: number
  status: string
  stages: TraceStage[]
  metadata: Record<string, JsonValue>
}

export interface TraceListResponse {
  trace_type: string
  traces: TraceRecord[]
  malformed_line_count: number
}

export interface EvaluationOptions {
  backends: string[]
  golden_test_sets: string[]
}

export interface EvaluationReport {
  run_id: string
  metrics: Record<string, number>
  cases: Record<string, JsonValue>[]
  metadata: Record<string, JsonValue>
}

export interface IngestionOptions {
  collections: string[]
  ai_enrichment_default: boolean
}

export type IngestionStatus = 'queued' | 'running' | 'success' | 'skipped' | 'failed'

export interface IngestionJob {
  job_id: string
  source_path: string
  collection: string
  status: IngestionStatus | string
  stage: string
  step: number
  total: number
  active: boolean
  result: Record<string, JsonValue> | null
  error: string | null
  started_at: number | null
}

export interface HealthResponse {
  status: string
}

export interface ApiErrorShape {
  detail?: string
  message?: string
}
