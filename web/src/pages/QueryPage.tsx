import { Activity, CaseSensitive, ChevronDown, Image, LoaderCircle, Search, TextSearch } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Badge, Button, EmptyState, ErrorState, Toggle } from '../components/ui'
import { PageHeader } from '../layouts/AppShell'
import { api } from '../lib/api'
import { getErrorMessage } from '../lib/format'
import type { GrepMatch, GrepResponse, JsonValue, QueryResponse, QueryResult, QueryScoreStage } from '../lib/types'
import { grepPatternLength, queryImageDataUrl } from '../lib/view-model'

type QueryMode = 'semantic' | 'grep'

export function QueryPage() {
  const [collections, setCollections] = useState<string[]>([])
  const [collection, setCollection] = useState('default')
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<QueryMode>('semantic')
  const [grepAvailable, setGrepAvailable] = useState(false)
  const [semanticTopK, setSemanticTopK] = useState(5)
  const [grepTopK, setGrepTopK] = useState(20)
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [semanticResponse, setSemanticResponse] = useState<QueryResponse | null>(null)
  const [grepResponse, setGrepResponse] = useState<GrepResponse | null>(null)
  const [semanticLoading, setSemanticLoading] = useState(false)
  const [grepLoading, setGrepLoading] = useState(false)
  const [semanticError, setSemanticError] = useState('')
  const [grepError, setGrepError] = useState('')

  useEffect(() => {
    api.getOverview().then((overview) => {
      const names = overview.collections.map((item) => item.name)
      setCollections(names)
      setCollection((current) => names.includes(current) ? current : names[0] ?? current)
      setGrepAvailable(overview.capabilities?.grep === true)
    }).catch(() => undefined)
  }, [])

  const options = collections.length ? collections : ['default']
  const loading = mode === 'semantic' ? semanticLoading : grepLoading
  const anyLoading = semanticLoading || grepLoading
  const topK = mode === 'semantic' ? semanticTopK : grepTopK
  const patternLength = grepPatternLength(query)
  const patternReady = patternLength >= 3 && patternLength <= 4000

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (anyLoading) return
    if (mode === 'semantic') {
      const normalized = query.trim()
      if (!normalized) return
      setSemanticLoading(true)
      setSemanticError('')
      try {
        setSemanticResponse(await api.queryKnowledge(normalized, collection, semanticTopK))
      } catch (reason) {
        setSemanticError(getErrorMessage(reason))
      } finally {
        setSemanticLoading(false)
      }
      return
    }
    if (!patternReady) return
    setGrepLoading(true)
    setGrepError('')
    try {
      setGrepResponse(await api.grepKnowledge(query, collection, grepTopK, caseSensitive))
    } catch (reason) {
      setGrepError(getErrorMessage(reason))
    } finally {
      setGrepLoading(false)
    }
  }

  return <div className="page-stack">
    <PageHeader eyebrow="知识库" title="知识检索" />
    {grepAvailable ? <div className="query-mode-switch" role="tablist" aria-label="检索模式">
      <button type="button" role="tab" aria-selected={mode === 'semantic'} className={mode === 'semantic' ? 'active' : ''} disabled={anyLoading} onClick={() => setMode('semantic')}><Search size={15} />语义检索</button>
      <button type="button" role="tab" aria-selected={mode === 'grep'} className={mode === 'grep' ? 'active' : ''} disabled={anyLoading} onClick={() => setMode('grep')}><TextSearch size={15} />精确查找</button>
    </div> : null}
    <form className={`card query-console ${mode === 'grep' ? 'query-console-grep' : ''}`} onSubmit={submit}>
      <label className="field query-text-field"><span>查询内容</span><div className="input-with-icon"><Search size={17} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入查询内容" required minLength={mode === 'grep' ? 3 : 1} maxLength={mode === 'grep' ? undefined : 4000} autoFocus /></div></label>
      <label className="field"><span>Collection</span><div className="select-wrap select-wrap-full"><select value={collection} onChange={(event) => setCollection(event.target.value)}>{options.map((name) => <option key={name} value={name}>{name}</option>)}</select><ChevronDown size={15} /></div></label>
      <label className="field"><span>Top K</span><input type="number" min={1} max={20} value={topK} onChange={(event) => mode === 'semantic' ? setSemanticTopK(event.currentTarget.valueAsNumber || 1) : setGrepTopK(event.currentTarget.valueAsNumber || 1)} /></label>
      {mode === 'grep' ? <label className="field query-case-field"><span>区分大小写</span><span className="query-case-control"><CaseSensitive size={17} /><Toggle checked={caseSensitive} onChange={setCaseSensitive} label="区分大小写" /></span></label> : null}
      <Button className="query-submit" type="submit" disabled={anyLoading || (mode === 'semantic' ? !query.trim() : !patternReady)}>{loading ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />}{loading ? '检索中' : '检索'}</Button>
    </form>
    {mode === 'semantic'
      ? <QueryResults response={semanticResponse} loading={semanticLoading} error={semanticError} />
      : <GrepResults response={grepResponse} loading={grepLoading} error={grepError} />}
  </div>
}

function QueryResults({ response, loading, error }: { response: QueryResponse | null; loading: boolean; error: string }) {
  if (error) return <section className="card query-state"><ErrorState message={error} /></section>
  if (loading) return <section className="card query-state"><div className="loading-state"><LoaderCircle className="spin" size={20} /><span>正在检索知识库</span></div></section>
  if (!response) return <section className="card query-state"><EmptyState icon={<Search size={24} />} title="等待检索" description="暂无检索结果。" /></section>
  if (!response.results.length) return <section className="card query-state"><EmptyState icon={<Search size={24} />} title="未找到相关内容" description={`Collection：${String(response.metadata.collection ?? 'default')}`} /></section>

  return <section className="query-results-section">
    <div className="results-heading"><div><span className="eyebrow">检索结果</span><h2>{response.results.length} 个相关片段</h2>{response.trace_id ? <p>Trace ID <code>{response.trace_id}</code></p> : null}</div></div>
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
    <ScoreStages stages={result.score_stages} />
    <dl className="query-citation-grid"><div><dt>Chunk ID</dt><dd title={result.chunk_id}>{result.chunk_id}</dd></div><div><dt>来源</dt><dd>{result.source || '—'}</dd></div><div><dt>页码</dt><dd>{result.page ?? '—'}</dd></div></dl>
    {metadata.length ? <details className="query-metadata"><summary>引用元数据</summary><dl>{metadata.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{formatMetadata(value)}</dd></div>)}</dl></details> : null}
    {images.length ? <div className="query-images"><div className="query-images-heading"><Image size={15} /><span>{images.length} 张关联图片</span></div><div>{images.map((item, index) => <figure key={`${item.image_id}-${index}`}><img src={item.url} alt={`${result.source || '检索结果'}关联图片`} loading="lazy" /><figcaption>{item.image_id}</figcaption></figure>)}</div></div> : null}
  </article>
}

function GrepResults({ response, loading, error }: { response: GrepResponse | null; loading: boolean; error: string }) {
  if (error) return <section className="card query-state"><ErrorState message={error} /></section>
  if (loading) return <section className="card query-state"><div className="loading-state"><LoaderCircle className="spin" size={20} /><span>正在精确查找</span></div></section>
  if (!response) return <section className="card query-state"><EmptyState icon={<TextSearch size={24} />} title="等待精确查找" description="暂无检索结果。" /></section>
  if (!response.matches.length) return <section className="card query-state"><EmptyState icon={<TextSearch size={24} />} title="未找到精确匹配" description={response.timed_out ? '检索超时，当前为部分结果。' : '当前 Collection 中没有匹配片段。'} /></section>

  return <section className="query-results-section">
    <div className="results-heading"><div><span className="eyebrow">精确查找结果</span><h2>{response.matches.length} 个匹配片段</h2>{response.timed_out ? <p className="grep-result-notice">检索超时，当前为部分结果</p> : response.truncated ? <p className="grep-result-notice">结果已截断</p> : null}{response.trace_id ? <p>Trace ID <code>{response.trace_id}</code></p> : null}</div></div>
    <div className="query-result-list">{response.matches.map((match) => <GrepResultCard key={match.chunk_id} match={match} />)}</div>
  </section>
}

function GrepResultCard({ match }: { match: GrepMatch }) {
  const metadata = Object.entries(match.metadata)
  return <article className="card query-result-card">
    <header className="grep-result-header"><div><strong>{match.source || '未知来源'}</strong><span>{match.page === null ? '页码未记录' : `第 ${match.page} 页`}</span></div><Badge tone="info">命中 {match.match_count} 次</Badge></header>
    <p className="query-result-text">{match.text}</p>
    <dl className="query-citation-grid"><div><dt>Chunk ID</dt><dd title={match.chunk_id}>{match.chunk_id}</dd></div><div><dt>来源</dt><dd>{match.source || '—'}</dd></div><div><dt>页码</dt><dd>{match.page ?? '—'}</dd></div></dl>
    {metadata.length ? <details className="query-metadata"><summary>引用元数据</summary><dl>{metadata.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{formatMetadata(value)}</dd></div>)}</dl></details> : null}
  </article>
}

const stageLabels: Record<QueryScoreStage['stage'], string> = {
  dense: 'Dense',
  bm25: 'BM25',
  fusion: 'RRF',
  rerank: 'Rerank',
}

const statusLabels: Record<QueryScoreStage['status'], string> = {
  hit: '已召回',
  not_recalled: '未召回',
  success: '已计算',
  skipped: '未启用',
  fallback: '已回退',
  ranking_only: '仅排序',
  unavailable: '无数据',
  error: '异常',
}

function ScoreStages({ stages }: { stages: QueryScoreStage[] }) {
  return <div className="query-score-breakdown"><div className="query-score-heading"><Activity size={15} /><span>阶段分数</span></div><div className="query-score-grid">{stages.map((stage) => <div className="query-score-stage" key={stage.stage}><div><strong>{stageLabels[stage.stage]}</strong><Badge tone={stage.status === 'hit' || stage.status === 'success' ? 'success' : stage.status === 'fallback' ? 'warning' : stage.status === 'error' ? 'danger' : 'neutral'}>{statusLabels[stage.status]}</Badge></div><span>{stage.score === null ? '—' : formatScore(stage.score)}</span><small>{[stage.score_kind !== 'none' ? stage.score_kind : null, stage.rank === null ? null : `Rank ${stage.rank}`, stage.k === null ? null : `k=${stage.k}`].filter(Boolean).join(' · ') || '无数值分数'}</small>{stage.rrf_contribution === null ? null : <small>RRF 贡献 {formatScore(stage.rrf_contribution)}</small>}{stage.backend ? <small>{stage.backend}</small> : null}</div>)}</div></div>
}

function formatScore(score: number): string {
  return Number.isFinite(score) ? score.toFixed(4) : '—'
}

function formatMetadata(value: JsonValue): string {
  if (value === null) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
