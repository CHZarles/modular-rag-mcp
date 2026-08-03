import {
  Activity,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Database,
  FileText,
  Gauge,
  Image,
  Layers3,
  Pencil,
  Plus,
  Settings2,
  Sparkles,
} from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { api } from '../lib/api'
import { getErrorMessage, formatNumber, titleCase } from '../lib/format'
import type { ComponentSummary, JsonValue, Overview } from '../lib/types'
import { PageHeader, SectionHeading } from '../layouts/AppShell'
import { Badge, Button, ErrorState, IconButton, LoadingState, Modal, Toggle, useToast } from '../components/ui'

const componentIcons: Record<string, typeof Sparkles> = {
  GEN: Sparkles,
  EMB: Activity,
  SPLIT: Layers3,
  RANK: Gauge,
  STORE: Database,
  EVAL: CircleDot,
}

const componentColors: Record<string, string> = {
  GEN: 'indigo',
  EMB: 'cyan',
  SPLIT: 'violet',
  RANK: 'amber',
  STORE: 'emerald',
  EVAL: 'rose',
}

export function OverviewPage() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [collectionName, setCollectionName] = useState('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<ComponentSummary | null>(null)
  const { showToast } = useToast()

  const loadOverview = () => {
    setLoading(true)
    setError('')
    api.getOverview().then(setOverview).catch((reason: unknown) => setError(getErrorMessage(reason))).finally(() => setLoading(false))
  }

  useEffect(() => { loadOverview() }, [])

  const createCollection = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!collectionName.trim()) return
    setCreating(true)
    try {
      await api.createCollection(collectionName.trim())
      setCollectionName('')
      showToast('已创建 Collection', 'success')
      loadOverview()
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    } finally {
      setCreating(false)
    }
  }

  const toggleComponent = async (component: ComponentSummary, enabled: boolean) => {
    try {
      await api.setComponentEnabled(component.code, enabled)
      showToast(`${component.label} 已${enabled ? '启用' : '停用'}`, 'success')
      loadOverview()
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    }
  }

  if (loading && !overview) return <><PageHeader eyebrow="指挥中心" title="总览" description="一览检索系统、数据资产与运行时 Provider 的整体状态。" /><LoadingState /></>
  if (error && !overview) return <><PageHeader eyebrow="指挥中心" title="总览" /><ErrorState message={error} onRetry={loadOverview} /></>
  if (!overview) return null

  const indexedCollections = overview.collections.filter((collection) => collection.indexed).length
  return <div className="page-stack">
    <section><SectionHeading eyebrow="资产" title="知识资产" description="展示当前工作区内已建索引的全部数据。" /><div className="metric-grid"><MetricCard icon={<FileText size={18} />} label="文档" value={overview.stats.document_count} detail="已建索引的源文件" tone="indigo" /><MetricCard icon={<Layers3 size={18} />} label="Chunk" value={overview.stats.chunk_count} detail="可用于检索的单元" tone="cyan" /><MetricCard icon={<Image size={18} />} label="图片" value={overview.stats.image_count} detail="多模态资产数量" tone="violet" /><MetricCard icon={<Database size={18} />} label="Collection" value={overview.collections.length} detail={`当前已建索引 ${indexedCollections} 个`} tone="emerald" /></div></section>
    <section className="grid-two"><div className="card collection-card"><SectionHeading eyebrow="知识资产" title="Collection 管理" description="在入库前为新数据创建独立的命名空间。" /><form className="collection-form" onSubmit={createCollection}><div className="input-with-icon"><Plus size={17} /><input value={collectionName} onChange={(event) => setCollectionName(event.target.value)} placeholder="例如 product-docs" aria-label="新建 Collection 名称" /></div><Button type="submit" disabled={creating || !collectionName.trim()}>{creating ? '创建中…' : '创建 Collection'}</Button></form><div className="collection-list">{overview.collections.map((collection) => <div className="collection-row" key={collection.name}><div className="collection-name"><span className={`collection-mark ${collection.indexed ? 'collection-mark-indexed' : ''}`}><Database size={14} /></span><strong>{collection.name}</strong></div><Badge tone={collection.indexed ? 'success' : 'neutral'}>{collection.indexed ? '已建索引' : '空'}</Badge></div>)}</div></div><div className="card runtime-card"><SectionHeading eyebrow="运行时" title="流水线状态" description="核心服务已配置完成，可随时处理请求。" /><div className="runtime-summary"><div className="runtime-score"><strong>6</strong><span>个组件在线</span></div><div className="runtime-bars"><div><span>配置完成度</span><ProgressLine value={94} color="indigo" /></div><div><span>索引可用率</span><ProgressLine value={indexedCollections ? 82 : 18} color="cyan" /></div><div><span>可观测性</span><ProgressLine value={76} color="violet" /></div></div></div><div className="runtime-foot"><span><span className="health-dot health-connected" /> 全部系统运行正常</span><span>刚刚更新</span></div></div></section>
    <section><SectionHeading eyebrow="流水线组件" title="运行时配置" description="按需配置各组件 Provider，或开关可选的生成与重排阶段。" /><div className="component-grid">{overview.components.map((component) => <ComponentCard component={component} key={component.code} onEdit={() => setEditing(component)} onToggle={(enabled) => toggleComponent(component, enabled)} />)}</div></section>
    <ConfigModal component={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); loadOverview() }} />
  </div>
}

function MetricCard({ icon, label, value, detail, tone }: { icon: ReactNode; label: string; value: number; detail: string; tone: string }) {
  return <div className="metric-card"><div className={`metric-icon metric-icon-${tone}`}>{icon}</div><div className="metric-copy"><span>{label}</span><strong>{formatNumber(value)}</strong><small>{detail}</small></div></div>
}

function ProgressLine({ value, color }: { value: number; color: string }) {
  return <div className="mini-progress"><span className={`mini-progress-fill fill-${color}`} style={{ width: `${value}%` }} /></div>
}

function ComponentCard({ component, onEdit, onToggle }: { component: ComponentSummary; onEdit: () => void; onToggle: (enabled: boolean) => void }) {
  const Icon = componentIcons[component.code] ?? Settings2
  const canToggle = component.code === 'GEN' || component.code === 'RANK'
  const [busy, setBusy] = useState(false)
  const handleToggle = async (enabled: boolean) => {
    setBusy(true)
    await onToggle(enabled)
    setBusy(false)
  }
  return <article className={`component-card component-card-${componentColors[component.code] ?? 'indigo'} ${!component.enabled ? 'component-disabled' : ''}`}><div className="component-card-top"><div className="component-title"><div className="component-icon"><Icon size={18} /></div><div><span className="component-code">{component.code}</span><h3>{component.label}</h3></div></div><IconButton onClick={onEdit} aria-label={`编辑 ${component.label}`}><Pencil size={16} /></IconButton></div><div className="component-provider"><span className="provider-label">Provider</span><strong>{component.provider}</strong>{component.model ? <span className="provider-model">{component.model}</span> : null}</div>{component.details.length ? <div className="component-details">{component.details.map((detail) => <div key={detail.label}><span>{detail.label}</span><strong>{detail.value}</strong></div>)}</div> : <div className="component-details component-details-empty"><span>配置已就绪</span></div>}<div className="component-card-footer">{canToggle ? <div className="toggle-control"><Toggle checked={component.enabled} onChange={handleToggle} label={`切换 ${component.label}`} /><span>{busy ? '更新中…' : component.enabled ? '已启用' : '已停用'}</span></div> : <Badge tone={component.enabled ? 'success' : 'neutral'}>{component.enabled ? '运行中' : '待启用'}</Badge>}<span className="component-edit-hint">编辑配置 <ChevronRight size={14} /></span></div></article>
}

function ConfigModal({ component, onClose, onSaved }: { component: ComponentSummary | null; onClose: () => void; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, JsonValue>>({})
  const [originalValues, setOriginalValues] = useState<Record<string, JsonValue>>({})
  const [providerOptions, setProviderOptions] = useState<string[]>([])
  const [apiKey, setApiKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const { showToast } = useToast()

  useEffect(() => {
    if (!component) return
    setLoading(true)
    setApiKey('')
    api.getComponent(component.code).then((response) => {
      setValues(response.values)
      setOriginalValues(response.values)
      setProviderOptions(response.provider_options)
    }).catch((reason: unknown) => showToast(getErrorMessage(reason), 'error')).finally(() => setLoading(false))
  }, [component, showToast])

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!component) return
    setSaving(true)
    try {
      const parsed = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, parseField(value, originalValues[key])]))
      await api.updateComponent(component.code, parsed, apiKey || undefined)
      showToast(`${component.label} 配置已保存`, 'success')
      onSaved()
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    } finally {
      setSaving(false)
    }
  }

  const keys = useMemo(() => Object.keys(values).filter((key) => key !== 'api_key' && key !== 'enabled'), [values])
  const isProviderKey = (key: string) => {
    if (!component) return false
    if (key !== 'provider' && key !== 'backend') return false
    if (component.code === 'GEN' && key === 'provider') return true
    if (component.code === 'EMB' && key === 'provider') return true
    if (component.code === 'RANK' && key === 'backend') return true
    return false
  }
  const ensureKnownProvider = (key: string, value: JsonValue): JsonValue => {
    if (!isProviderKey(key)) return value
    if (typeof value !== 'string') return value
    if (providerOptions.includes(value)) return value
    return providerOptions[0] ?? value
  }
  return <Modal open={Boolean(component)} onClose={onClose} title={`配置 ${component?.label ?? '组件'}`} eyebrow={`${component?.code ?? ''} · 运行时 Provider`}><form className="config-form" onSubmit={save}>{loading ? <LoadingState label="正在读取生效配置" /> : <>{<div className="config-intro"><div className="config-intro-icon"><Settings2 size={20} /></div><p>配置变更将作为本地运行时覆盖立即生效，密钥字段在保存后不会再次明文展示。</p></div>}<div className="form-grid">{keys.map((key) => <Field key={key} name={key} value={ensureKnownProvider(key, values[key])} options={isProviderKey(key) ? providerOptions : undefined} onChange={(value) => setValues((current) => ({ ...current, [key]: value }))} />)}</div>{(component?.code === 'GEN' || component?.code === 'EMB') ? <label className="field"><span>API 密钥 <small>选填 · 留空表示保留当前密钥</small></span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="请输入新的 API 密钥" autoComplete="new-password" /></label> : null}<div className="modal-actions"><Button type="button" className="button-secondary" onClick={onClose}>取消</Button><Button type="submit" disabled={saving}>{saving ? '保存中…' : <><Check size={16} /> 保存修改</>}</Button></div></>}</form></Modal>
}

function Field({ name, value, onChange, options }: { name: string; value: JsonValue; onChange: (value: JsonValue) => void; options?: string[] }) {
  if (options && options.length) {
    const stringValue = typeof value === 'string' ? value : value === null ? '' : String(value)
    return <label className="field"><span>{titleCase(name)}</span><div className="select-wrap select-wrap-full"><select value={stringValue} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select><ChevronDown size={15} /></div></label>
  }
  if (typeof value === 'boolean') return <label className="field field-checkbox"><span>{titleCase(name)}</span><Toggle checked={value} onChange={onChange} label={titleCase(name)} /></label>
  const displayValue = Array.isArray(value) ? value.join(', ') : value === null ? '' : String(value)
  return <label className="field"><span>{titleCase(name)}</span>{name === 'base_url' || name === 'endpoint' || name.includes('path') ? <input value={displayValue} onChange={(event) => onChange(event.target.value)} placeholder="尚未配置" /> : <input value={displayValue} onChange={(event) => onChange(event.target.value)} />}</label>
}

function parseField(value: JsonValue, original: JsonValue): JsonValue {
  if (typeof original === 'boolean') return value === true || value === 'true'
  if (typeof original === 'number') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : value
  }
  if (Array.isArray(original)) return String(value).split(',').map((item) => item.trim()).filter(Boolean)
  return value
}
