import type {
  ApiErrorShape,
  CollectionResponse,
  ComponentResponse,
  DocumentDetail,
  DocumentListResponse,
  EvaluationOptions,
  EvaluationReport,
  HealthResponse,
  HotpotQABenchmarkJob,
  HotpotQABenchmarkSummary,
  IngestionJob,
  IngestionOptions,
  Overview,
  QueryResponse,
  TraceListResponse,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })
  const contentType = response.headers.get('content-type') ?? ''
  const payload: unknown = contentType.includes('application/json')
    ? await response.json()
    : await response.text()
  if (!response.ok) {
    const errorPayload = payload as ApiErrorShape
    const message = typeof errorPayload === 'object' && errorPayload !== null
      ? errorPayload.detail ?? errorPayload.message
      : undefined
    throw new ApiError(message ?? `Request failed with status ${response.status}`, response.status)
  }
  return payload as T
}

function jsonBody(value: unknown): RequestInit {
  return { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) }
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  getOverview: () => request<Overview>('/api/overview'),
  queryKnowledge: (query: string, collection: string, topK: number) => request<QueryResponse>('/api/query', {
    method: 'POST',
    ...jsonBody({ query, collection, top_k: topK }),
  }),
  getComponent: (code: string) => request<ComponentResponse>(`/api/components/${encodeURIComponent(code)}`),
  updateComponent: (code: string, values: Record<string, unknown>, apiKey?: string) => request<ComponentResponse>(
    `/api/components/${encodeURIComponent(code)}`,
    { method: 'PUT', ...jsonBody({ values, ...(apiKey ? { api_key: apiKey } : {}) }) },
  ),
  setComponentEnabled: (code: string, enabled: boolean) => request<ComponentResponse>(
    `/api/components/${encodeURIComponent(code)}/enabled`,
    { method: 'PATCH', ...jsonBody({ enabled }) },
  ),
  createCollection: (name: string) => request<CollectionResponse>('/api/collections', {
    method: 'POST',
    ...jsonBody({ name }),
  }),
  getDocuments: (collection?: string) => request<DocumentListResponse>(
    `/api/documents${collection ? `?collection=${encodeURIComponent(collection)}` : ''}`,
  ),
  getDocument: (docId: string) => request<DocumentDetail>(`/api/documents/${encodeURIComponent(docId)}`),
  deleteDocument: (docId: string) => request<Record<string, unknown>>(`/api/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' }),
  getTraces: (type: 'ingestion' | 'query') => request<TraceListResponse>(`/api/traces/${type}`),
  getEvaluationOptions: () => request<EvaluationOptions>('/api/evaluation/options'),
  runEvaluation: (testSetPath: string, backends: string[]) => request<EvaluationReport>('/api/evaluation/runs', {
    method: 'POST',
    ...jsonBody({ test_set_path: testSetPath, backends }),
  }),
  getHotpotQABenchmark: () => request<HotpotQABenchmarkSummary>('/api/evaluation/benchmarks/hotpotqa'),
  getHotpotQABenchmarkRun: () => request<HotpotQABenchmarkJob | null>('/api/evaluation/benchmarks/hotpotqa/run'),
  runHotpotQABenchmark: (includeImages: boolean) => request<HotpotQABenchmarkJob>('/api/evaluation/benchmarks/hotpotqa', {
    method: 'POST',
    ...jsonBody({ include_images: includeImages }),
  }),
  getIngestionOptions: () => request<IngestionOptions>('/api/ingestion/options'),
  submitIngestion: (file: File, collection: string, force: boolean, aiEnrichment: boolean) => {
    const body = new FormData()
    body.append('file', file)
    body.append('collection', collection)
    body.append('force', String(force))
    body.append('ai_enrichment', String(aiEnrichment))
    return request<IngestionJob>('/api/ingestion/jobs', { method: 'POST', body })
  },
  getActiveJob: () => request<IngestionJob | null>('/api/ingestion/jobs/active'),
  getIngestionJob: (jobId: string) => request<IngestionJob>(`/api/ingestion/jobs/${encodeURIComponent(jobId)}`),
}
