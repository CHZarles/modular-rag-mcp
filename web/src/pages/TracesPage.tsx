import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  GitBranch,
  Search,
  SlidersHorizontal,
  Timer,
  X,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { formatDate, formatDuration, getErrorMessage, titleCase } from '../lib/format'
import type { TraceRecord, TraceStage } from '../lib/types'
import { PageHeader, SectionHeading } from '../layouts/AppShell'
import { Badge, EmptyState, ErrorState, IconButton, LoadingState, Modal } from '../components/ui'

type TraceKind = 'ingestion' | 'query'

export function IngestionTracesPage() {
  return <TracesPage kind="ingestion" />
}

export function QueryTracesPage() {
  return <TracesPage kind="query" />
}

function TracesPage({ kind }: { kind: TraceKind }) {
  const [traces, setTraces] = useState<TraceRecord[]>([])
  const [malformedCount, setMalformedCount] = useState(0)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [selected, setSelected] = useState<TraceRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    api.getTraces(kind).then((response) => { setTraces(response.traces); setMalformedCount(response.malformed_line_count) }).catch((reason: unknown) => setError(getErrorMessage(reason))).finally(() => setLoading(false))
  }, [kind])

  useEffect(() => { load() }, [load])

  const filtered = useMemo(() => traces.filter((trace) => {
    const matchesStatus = status === 'all' || normalizeStatus(trace.status) === status
    const haystack = [trace.trace_id, trace.status, ...Object.values(trace.metadata).map(String), ...trace.stages.map((stage) => `${stage.name} ${stage.provider}`)].join(' ').toLowerCase()
    return matchesStatus && haystack.includes(query.trim().toLowerCase())
  }), [query, status, traces])
  const maxDuration = Math.max(1, ...filtered.map((trace) => trace.total_elapsed_ms))
  const title = kind === 'ingestion' ? '入库追踪' : '查询追踪'
  const emptyTitle = kind === 'ingestion' ? '暂无入库追踪记录' : '暂无查询追踪记录'

  return <div className="page-stack"><PageHeader eyebrow="可观测性" title={title} actions={<button className="refresh-link" onClick={load}><Activity size={15} /> 刷新追踪</button>} /><section className="trace-summary-grid"><TraceSummary icon={<GitBranch size={18} />} label="追踪总数" value={traces.length} detail="当前日志中的记录数" /><TraceSummary icon={<Timer size={18} />} label="中位耗时" value={formatDuration(median(traces.map((trace) => trace.total_elapsed_ms)))} detail="覆盖全部可见操作" /><TraceSummary icon={<CheckCircle2 size={18} />} label="健康运行" value={traces.filter((trace) => isHealthy(trace.status)).length} detail="成功或已完成" /><TraceSummary icon={<AlertCircle size={18} />} label="解析告警" value={malformedCount} detail="被跳过的非法行数" /></section><section className="card traces-panel"><SectionHeading eyebrow="追踪浏览器" title="执行历史" /><div className="trace-toolbar"><div className="search-input"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Trace ID、Provider、元数据…" aria-label="搜索追踪" />{query ? <IconButton onClick={() => setQuery('')} aria-label="清空追踪搜索"><X size={15} /></IconButton> : null}</div><div className="select-wrap"><SlidersHorizontal size={16} /><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按状态筛选追踪"><option value="all">全部状态</option><option value="success">成功</option><option value="failed">失败</option><option value="unknown">未知</option></select></div></div>{loading ? <LoadingState label="正在读取追踪记录" /> : error ? <ErrorState message={error} onRetry={load} /> : filtered.length === 0 ? <EmptyState icon={<Database size={24} />} title={query || status !== 'all' ? '未找到匹配的追踪' : emptyTitle} description={query || status !== 'all' ? '请调整搜索关键词或状态筛选条件。' : '当可观测性日志接收到操作后，追踪记录将在此展示。'} /> : <div className="trace-list">{filtered.map((trace) => <TraceRow key={trace.trace_id} trace={trace} maxDuration={maxDuration} onSelect={() => setSelected(trace)} />)}</div>}</section><TraceDetailModal trace={selected} onClose={() => setSelected(null)} /></div>
}

function TraceSummary({ icon, label, value, detail }: { icon: ReactNode; label: string; value: number | string; detail: string }) {
  return <div className="card trace-summary"><div className="trace-summary-icon">{icon}</div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></div>
}

function TraceRow({ trace, maxDuration, onSelect }: { trace: TraceRecord; maxDuration: number; onSelect: () => void }) {
  const normalizedStatus = normalizeStatus(trace.status)
  const label = String(trace.metadata.source_path ?? trace.metadata.query ?? trace.trace_id)
  const statusBadge: Record<string, string> = {
    success: '成功',
    failed: '失败',
    unknown: '未知',
  }
  return <button className="trace-row" onClick={onSelect}><div className={`trace-status-icon trace-status-${normalizedStatus}`}>{normalizedStatus === 'success' ? <CheckCircle2 size={18} /> : normalizedStatus === 'failed' ? <XCircle size={18} /> : <Clock3 size={18} />}</div><div className="trace-main"><div className="trace-row-heading"><strong>{label}</strong><span className="trace-id">{trace.trace_id}</span></div><div className="trace-row-meta"><span>{formatDate(trace.started_at)}</span><span>{trace.stages.length} 个阶段</span><span>{trace.metadata.collection ? String(trace.metadata.collection) : '运行时'}</span></div></div><div className="duration-visual"><div className="duration-bar"><span style={{ width: `${Math.max(5, (trace.total_elapsed_ms / maxDuration) * 100)}%` }} /></div><strong>{formatDuration(trace.total_elapsed_ms)}</strong></div><Badge tone={normalizedStatus === 'success' ? 'success' : normalizedStatus === 'failed' ? 'danger' : 'neutral'}>{statusBadge[normalizedStatus] ?? trace.status}</Badge><ChevronRight size={17} className="row-chevron" /></button>
}

function TraceDetailModal({ trace, onClose }: { trace: TraceRecord | null; onClose: () => void }) {
  const [stage, setStage] = useState<TraceStage | null>(null)
  useEffect(() => { setStage(trace?.stages[0] ?? null) }, [trace])
  const statusBadge: Record<string, string> = {
    success: '成功',
    failed: '失败',
    unknown: '未知',
  }
  const normalizedStatus = trace ? normalizeStatus(trace.status) : 'unknown'
  return <Modal open={Boolean(trace)} onClose={onClose} title="追踪详情" eyebrow={`${trace?.trace_id ?? ''} · ${trace?.trace_type ?? ''}`} wide>{trace ? <div className="trace-detail"><div className="trace-detail-overview"><div><span className="eyebrow">操作</span><h3>{String(trace.metadata.source_path ?? trace.metadata.query ?? trace.trace_id)}</h3></div><div className="trace-detail-badges"><Badge tone={normalizedStatus === 'success' ? 'success' : normalizedStatus === 'failed' ? 'danger' : 'neutral'}>{statusBadge[normalizedStatus] ?? trace.status}</Badge><strong>{formatDuration(trace.total_elapsed_ms)}</strong></div></div><div className="stage-timeline">{trace.stages.map((item, index) => <button className={`stage-card ${stage?.name === item.name && stage?.timestamp === item.timestamp ? 'stage-card-active' : ''}`} key={`${item.name}-${item.timestamp}-${index}`} onClick={() => setStage(item)}><div className="stage-number">{String(index + 1).padStart(2, '0')}</div><div className="stage-card-main"><div><strong>{titleCase(item.name)}</strong><span>{item.method} · {item.provider}</span></div><span className="stage-duration">{item.elapsed_ms === null ? '—' : formatDuration(item.elapsed_ms)}</span></div><ChevronRight size={16} /></button>)}</div>{stage ? <StageInspector stage={stage} onClose={() => setStage(null)} /> : null}<div className="trace-metadata"><span className="eyebrow">追踪元数据</span><div className="metadata-grid">{Object.entries(trace.metadata).map(([key, value]) => <div key={key}><span>{titleCase(key)}</span><strong>{String(value)}</strong></div>)}</div></div></div> : null}</Modal>
}

function StageInspector({ stage, onClose }: { stage: TraceStage; onClose: () => void }) {
  return <div className="stage-inspector"><div className="stage-inspector-heading"><div><span className="eyebrow">阶段负载</span><h3>{titleCase(stage.name)}</h3></div><IconButton onClick={onClose} aria-label="关闭阶段详情"><X size={16} /></IconButton></div><div className="stage-inspector-grid"><div><span>方法</span><strong>{stage.method}</strong></div><div><span>Provider</span><strong>{stage.provider}</strong></div><div><span>耗时</span><strong>{stage.elapsed_ms === null ? '—' : formatDuration(stage.elapsed_ms)}</strong></div></div><pre>{JSON.stringify({ details: stage.details, data: stage.data }, null, 2)}</pre></div>
}

function normalizeStatus(status: string): string {
  const normalized = status.toLowerCase()
  if (['ok', 'success', 'completed', 'complete'].includes(normalized)) return 'success'
  if (['error', 'failed', 'failure'].includes(normalized)) return 'failed'
  return 'unknown'
}

function isHealthy(status: string): boolean {
  return normalizeStatus(status) === 'success'
}

function median(values: number[]): number {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}
