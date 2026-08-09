import { ChevronDown, Image, LoaderCircle, Search } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Button, EmptyState, ErrorState, Badge } from '../components/ui'
import { api } from '../lib/api'
import { getErrorMessage } from '../lib/format'
import type { JsonValue, QueryResponse, QueryResult } from '../lib/types'
import { queryImageDataUrl } from '../lib/view-model'
import { PageHeader } from '../layouts/AppShell'

export function QueryPage() {
  const [collections, setCollections] = useState<string[]>([])
  const [collection, setCollection] = useState('default')
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(5)
  const [response, setResponse] = useState<QueryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getOverview().then((overview) => {
      const names = overview.collections.map((item) => item.name)
      setCollections(names)
      setCollection((current) => names.includes(current) ? current : names[0] ?? current)
    }).catch(() => undefined)
  }, [])

  const options = collections.length ? collections : ['default']

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = query.trim()
    if (!normalized || loading) return
    setLoading(true)
    setError('')
    try {
      setResponse(await api.queryKnowledge(normalized, collection, topK))
    } catch (reason) {
      setError(getErrorMessage(reason))
    } finally {
      setLoading(false)
    }
  }

  return <div className="page-stack">
    <PageHeader eyebrow="知识库" title="知识检索" description="检索已建索引的知识内容并核对来源引用。" />
    <form className="card query-console" onSubmit={submit}>
      <label className="field query-text-field"><span>查询内容</span><div className="input-with-icon"><Search size={17} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入查询内容" required maxLength={4000} autoFocus /></div></label>
      <label className="field"><span>Collection</span><div className="select-wrap select-wrap-full"><select value={collection} onChange={(event) => setCollection(event.target.value)}>{options.map((name) => <option key={name} value={name}>{name}</option>)}</select><ChevronDown size={15} /></div></label>
      <label className="field"><span>Top K</span><input type="number" min={1} max={20} value={topK} onChange={(event) => setTopK(event.currentTarget.valueAsNumber || 1)} /></label>
      <Button className="query-submit" type="submit" disabled={loading || !query.trim()}>{loading ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />}{loading ? '检索中' : '检索'}</Button>
    </form>
    <QueryResults response={response} loading={loading} error={error} />
  </div>
}

function QueryResults({ response, loading, error }: { response: QueryResponse | null; loading: boolean; error: string }) {
  if (error) return <section className="card query-state"><ErrorState message={error} /></section>
  if (loading) return <section className="card query-state"><div className="loading-state"><LoaderCircle className="spin" size={20} /><span>正在检索知识库</span></div></section>
  if (!response) return <section className="card query-state"><EmptyState icon={<Search size={24} />} title="等待检索" description="暂无检索结果。" /></section>
  if (!response.results.length) return <section className="card query-state"><EmptyState icon={<Search size={24} />} title="未找到相关内容" description={`Collection：${String(response.metadata.collection ?? 'default')}`} /></section>

  return <section className="query-results-section">
    <div className="results-heading"><div><span className="eyebrow">检索结果</span><h2>{response.results.length} 个相关片段</h2>{response.trace_id ? <p>Trace ID <code>{response.trace_id}</code></p> : null}</div><Badge tone="success">已完成</Badge></div>
    <div className="query-result-list">{response.results.map((result) => <ResultCard key={`${result.chunk_id}-${result.rank}`} result={result} content={response.content} />)}</div>
  </section>
}

function ResultCard({ result, content }: { result: QueryResult; content: QueryResponse['content'] }) {
  const images = result.images.flatMap((reference) => {
    const url = queryImageDataUrl(content, reference.content_index)
    return url ? [{ ...reference, url }] : []
  })
  const metadata = Object.entries(result.metadata)

  return <article className="card query-result-card">
    <header className="query-result-header"><span className="query-rank">{result.rank}</span><div><strong>{result.source || '未知来源'}</strong><span>{result.page === null ? '页码未记录' : `第 ${result.page} 页`}</span></div><div className="query-score"><Badge tone="info">{result.score_kind}</Badge><strong>{formatScore(result.score)}</strong></div></header>
    <p className="query-result-text">{result.text}</p>
    <dl className="query-citation-grid"><div><dt>Chunk ID</dt><dd title={result.chunk_id}>{result.chunk_id}</dd></div><div><dt>来源</dt><dd>{result.source || '—'}</dd></div><div><dt>页码</dt><dd>{result.page ?? '—'}</dd></div></dl>
    {metadata.length ? <details className="query-metadata"><summary>引用元数据</summary><dl>{metadata.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{formatMetadata(value)}</dd></div>)}</dl></details> : null}
    {images.length ? <div className="query-images"><div className="query-images-heading"><Image size={15} /><span>{images.length} 张关联图片</span></div><div>{images.map((item, index) => <figure key={`${item.image_id}-${index}`}><img src={item.url} alt={`${result.source || '检索结果'}关联图片`} loading="lazy" /><figcaption>{item.image_id}</figcaption></figure>)}</div></div> : null}
  </article>
}

function formatScore(score: number): string {
  return Number.isFinite(score) ? score.toFixed(4) : '—'
}

function formatMetadata(value: JsonValue): string {
  if (value === null) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
