import {
  ChevronDown,
  ChevronRight,
  FileText,
  Filter,
  FolderOpen,
  Grid2X2,
  List,
  Layers3,
  Search,
  Tags,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { compactPath, formatNumber, getErrorMessage } from '../lib/format'
import type { ChunkDetail, DocumentDetail, DocumentSummary, DocumentListResponse, Overview } from '../lib/types'
import { PageHeader } from '../layouts/AppShell'
import { Badge, Button, EmptyState, ErrorState, IconButton, LoadingState, Modal, Skeleton, useToast } from '../components/ui'

export function DataBrowserPage() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [payload, setPayload] = useState<DocumentListResponse | null>(null)
  const [collection, setCollection] = useState('')
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'table' | 'cards'>('table')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<DocumentSummary | null>(null)

  useEffect(() => {
    api.getOverview().then(setOverview).catch(() => undefined)
  }, [])

  const loadDocuments = useCallback(() => {
    setLoading(true)
    setError('')
    api.getDocuments(collection || undefined).then(setPayload).catch((reason: unknown) => setError(getErrorMessage(reason))).finally(() => setLoading(false))
  }, [collection])

  useEffect(() => { loadDocuments() }, [loadDocuments])

  const filteredDocuments = useMemo(() => {
    const documents = payload?.documents ?? []
    const normalized = query.trim().toLowerCase()
    if (!normalized) return documents
    return documents.filter((document) => [document.title, document.summary, document.source_path, document.doc_id, ...document.tags].filter(Boolean).join(' ').toLowerCase().includes(normalized))
  }, [payload?.documents, query])

  return <div className="page-stack"><PageHeader eyebrow="知识资产" title="数据浏览" description="查看已建索引的源文件、元数据，以及支撑检索的 Chunk。" actions={<div className="view-switcher"><IconButton className={view === 'table' ? 'view-active' : ''} onClick={() => setView('table')} aria-label="表格视图"><List size={17} /></IconButton><IconButton className={view === 'cards' ? 'view-active' : ''} onClick={() => setView('cards')} aria-label="卡片视图"><Grid2X2 size={17} /></IconButton></div>} /><section className="card browser-toolbar"><div className="search-input"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文档、路径或标签…" aria-label="搜索文档" />{query ? <IconButton onClick={() => setQuery('')} aria-label="清空搜索"><X size={15} /></IconButton> : null}</div><div className="select-wrap"><Filter size={16} /><select value={collection} onChange={(event) => setCollection(event.target.value)} aria-label="按 Collection 筛选"><option value="">全部 Collection</option>{(overview?.collections ?? []).map((item) => <option value={item.name} key={item.name}>{item.name}</option>)}</select><ChevronDown size={15} /></div><div className="toolbar-result"><strong>{formatNumber(filteredDocuments.length)}</strong><span>份文档</span></div></section>{payload ? <section className="browser-stats"><StatPill icon={<FileText size={16} />} label="文档" value={payload.stats.document_count} /><StatPill icon={<FolderOpen size={16} />} label="Chunk" value={payload.stats.chunk_count} /><StatPill icon={<Tags size={16} />} label="图片" value={payload.stats.image_count} /></section> : null}{error ? <ErrorState message={error} onRetry={loadDocuments} /> : loading ? <DocumentSkeleton /> : filteredDocuments.length === 0 ? <EmptyState icon={<FolderOpen size={24} />} title={query ? '未找到匹配的文档' : '尚无已建索引的文档'} description={query ? '请尝试更宽泛的搜索词或调整 Collection 筛选条件。' : '请前往入库管理上传 PDF，开始构建 Collection。'} action={query ? <Button className="button-secondary" onClick={() => setQuery('')}>清空搜索</Button> : undefined} /> : view === 'table' ? <DocumentTable documents={filteredDocuments} onSelect={setSelected} /> : <DocumentCards documents={filteredDocuments} onSelect={setSelected} />}<DocumentDetailModal document={selected} onClose={() => setSelected(null)} /></div>
}

function StatPill({ icon, label, value }: { icon: ReactNode; label: string; value: number }) {
  return <div className="stat-pill"><span>{icon}</span><div><strong>{formatNumber(value)}</strong><small>{label}</small></div></div>
}

function DocumentTable({ documents, onSelect }: { documents: DocumentSummary[]; onSelect: (document: DocumentSummary) => void }) {
  return <div className="card table-card"><table><thead><tr><th>文档</th><th>Collection</th><th>Chunk</th><th>图片</th><th>索引状态</th><th /></tr></thead><tbody>{documents.map((document) => <tr key={document.doc_id} onClick={() => onSelect(document)}><td><div className="document-cell"><div className="file-icon"><FileText size={17} /></div><div><strong>{document.title || document.source_path.split('/').pop() || document.doc_id}</strong><span>{compactPath(document.source_path)}</span></div></div></td><td><Badge tone="info">{String(document.metadata.collection ?? 'default')}</Badge></td><td>{String(document.metadata.chunk_count ?? '—')}</td><td>{String(document.metadata.image_count ?? '0')}</td><td><span className="indexed-status"><span className="health-dot health-connected" /> 就绪</span></td><td><ChevronRight size={17} className="row-chevron" /></td></tr>)}</tbody></table></div>
}

function DocumentCards({ documents, onSelect }: { documents: DocumentSummary[]; onSelect: (document: DocumentSummary) => void }) {
  return <div className="document-card-grid">{documents.map((document) => <button className="card document-card" key={document.doc_id} onClick={() => onSelect(document)}><div className="document-card-top"><div className="file-icon"><FileText size={17} /></div><ChevronRight size={16} /></div><h3>{document.title || document.source_path.split('/').pop() || document.doc_id}</h3><p>{document.summary || compactPath(document.source_path)}</p><div className="document-card-meta"><Badge tone="info">{String(document.metadata.collection ?? 'default')}</Badge><span>{String(document.metadata.chunk_count ?? 0)} 个 Chunk</span></div>{document.tags.length ? <div className="tag-list">{document.tags.slice(0, 3).map((tag) => <span key={tag}>#{tag}</span>)}</div> : null}</button>)}</div>
}

function DocumentSkeleton() {
  return <div className="card table-card"><div className="skeleton-table">{[1, 2, 3, 4].map((item) => <div className="skeleton-row" key={item}><Skeleton className="skeleton-avatar" /><Skeleton className="skeleton-line skeleton-line-wide" /><Skeleton className="skeleton-line" /><Skeleton className="skeleton-line" /></div>)}</div></div>
}

function DocumentDetailModal({ document, onClose }: { document: DocumentSummary | null; onClose: () => void }) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const { showToast } = useToast()
  useEffect(() => {
    if (!document) return
    setLoading(true)
    setDetail(null)
    api.getDocument(document.doc_id).then(setDetail).catch((reason: unknown) => showToast(getErrorMessage(reason), 'error')).finally(() => setLoading(false))
  }, [document, showToast])
  const chunks = getChunks(detail)
  const summary = detail?.document
  return <Modal open={Boolean(document)} onClose={onClose} title={summary?.title || summary?.source_path.split('/').pop() || '文档详情'} eyebrow="已建索引文档" wide>{loading ? <LoadingState label="正在加载文档与 Chunk" /> : detail && summary ? <div className="detail-layout"><div className="detail-summary"><div className="detail-file-heading"><div className="file-icon file-icon-large"><FileText size={22} /></div><div><span className="eyebrow">源文件路径</span><strong>{summary.source_path}</strong></div></div><p>{summary.summary || '该文档未保存摘要。'}</p><div className="detail-kpis"><KeyMetric label="文档 ID" value={summary.doc_id} /><KeyMetric label="Collection" value={String(summary.metadata.collection ?? 'default')} /><KeyMetric label="Chunk 数" value={String(summary.metadata.chunk_count ?? chunks.length)} /></div>{summary.tags.length ? <div className="tag-list tag-list-large">{summary.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div> : null}</div><div className="chunks-panel"><div className="chunks-heading"><div><span className="eyebrow">检索单元</span><h3>共 {chunks.length} 个 Chunk</h3></div><Badge tone="success">已建索引</Badge></div>{chunks.length ? <div className="chunk-list">{chunks.map((chunk, index) => <div className="chunk-item" key={String(chunk.id ?? chunk.chunk_id ?? index)}><div className="chunk-index">{String(chunk.chunk_index ?? index + 1).padStart(2, '0')}</div><div><p>{String(chunk.text ?? chunk.content ?? '暂无 Chunk 文本内容。')}</p><span>{String(chunk.source_ref ?? chunk.id ?? chunk.chunk_id ?? '源片段')}</span></div></div>)}</div> : <EmptyState icon={<Layers3 size={21} />} title="未返回 Chunk 详情" description="API 未提供该文档的 Chunk 明细。" />}</div></div> : null}</Modal>
}

function getChunks(detail: DocumentDetail | null): ChunkDetail[] {
  return detail?.chunks ?? []
}

function KeyMetric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong title={value}>{value}</strong></div>
}
