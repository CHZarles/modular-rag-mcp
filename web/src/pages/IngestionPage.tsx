import {
  BrainCircuit,
  Check,
  CircleAlert,
  CloudUpload,
  FileSpreadsheet,
  FileText,
  FileType2,
  FolderOpen,
  Gauge,
  HardDrive,
  Image as ImageIcon,
  RefreshCw,
  Split,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { Badge, Button, EmptyState, ErrorState, IconButton, LoadingState, ProgressBar, Toggle, useToast } from '../components/ui'
import { PageHeader, SectionHeading } from '../layouts/AppShell'
import { api } from '../lib/api'
import { compactPath, formatDate, formatNumber, getErrorMessage } from '../lib/format'
import type { DocumentListResponse, IngestionJob, IngestionOptions } from '../lib/types'
import { selectAvailableValue, validateUploadCandidate } from '../lib/view-model'

export function IngestionPage() {
  const [options, setOptions] = useState<IngestionOptions | null>(null)
  const [documents, setDocuments] = useState<DocumentListResponse | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [collection, setCollection] = useState('default')
  const [aiEnrichment, setAiEnrichment] = useState(false)
  const [force, setForce] = useState(false)
  const [job, setJob] = useState<IngestionJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const { showToast } = useToast()

  const loadDocuments = useCallback(() => {
    api.getDocuments().then(setDocuments).catch((reason: unknown) => showToast(getErrorMessage(reason), 'error'))
  }, [showToast])

  useEffect(() => {
    Promise.all([api.getIngestionOptions(), api.getActiveJob(), api.getDocuments()])
      .then(([nextOptions, activeJob, docs]) => {
        setOptions(nextOptions)
        setAiEnrichment(nextOptions.ai_enrichment_default)
        setJob(activeJob)
        setDocuments(docs)
        setCollection((current) => selectAvailableValue(current, nextOptions.collections))
      })
      .catch((reason: unknown) => setError(getErrorMessage(reason)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!job?.active) {
      setPollError('')
      return
    }
    let cancelled = false
    let timer: number
    const poll = async () => {
      try {
        const nextJob = await api.getIngestionJob(job.job_id)
        if (cancelled) return
        setJob(nextJob)
        setPollError('')
        if (!nextJob.active) loadDocuments()
        else timer = window.setTimeout(poll, 900)
      } catch (reason) {
        if (cancelled) return
        setPollError(getErrorMessage(reason))
        timer = window.setTimeout(poll, 3000)
      }
    }
    timer = window.setTimeout(poll, 900)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [job?.active, job?.job_id, loadDocuments])

  const clearFile = () => {
    setFile(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const chooseFile = (candidate: File | undefined) => {
    if (!candidate || !options) return
    const validation = validateUploadCandidate(
      candidate.name,
      candidate.size,
      options.accepted_extensions,
      options.max_upload_bytes,
    )
    if (validation === 'unsupported_file_type') {
      showToast(`仅支持 ${formatExtensions(options.accepted_extensions)}。`, 'error')
      clearFile()
      return
    }
    if (validation === 'file_too_large') {
      showToast(`文件不能超过 ${formatBytes(options.max_upload_bytes)}。`, 'error')
      clearFile()
      return
    }
    setFile(candidate)
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    chooseFile(event.dataTransfer.files[0])
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!file || !collection) return
    setSubmitting(true)
    try {
      const created = await api.submitIngestion(file, collection, force, aiEnrichment)
      setJob(created)
      clearFile()
      showToast('入库任务已加入队列', 'success')
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const removeDocument = async (docId: string) => {
    if (!window.confirm('确定删除该文档及其已建索引的 Chunk 吗？')) return
    try {
      await api.deleteDocument(docId)
      showToast('已从索引中移除文档', 'success')
      loadDocuments()
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    }
  }

  if (loading) return <><PageHeader eyebrow="数据流水线" title="入库管理" /><LoadingState /></>
  if (error && !options) return <><PageHeader eyebrow="数据流水线" title="入库管理" /><ErrorState message={error} /></>
  if (!options) return null

  const progress = job && job.total > 0 ? (job.step / job.total) * 100 : job?.active ? 12 : 0
  const imageSelected = file ? isImageFile(file.name) : false
  return <div className="page-stack">
    <PageHeader eyebrow="数据流水线" title="入库管理" />
    <section className="ingestion-layout">
      <form className="card upload-card" onSubmit={submit}>
        <SectionHeading eyebrow="新建入库" title="添加资料" />
        <div className="ingestion-capabilities" aria-label="当前入库配置">
          <Capability icon={<FileText size={15} />} label="格式" value={formatExtensions(options.accepted_extensions)} />
          <Capability icon={<Gauge size={15} />} label="上限" value={formatBytes(options.max_upload_bytes)} />
          <Capability icon={<FileType2 size={15} />} label="PDF" value={options.pdf_loader_provider} />
          <Capability icon={<BrainCircuit size={15} />} label="图片描述" value={formatProvider(options.image_caption_provider, options.image_caption_model)} />
          <Capability icon={<Split size={15} />} label="切分" value={options.splitter_provider ?? '未配置'} />
        </div>
        <div
          className={`drop-zone ${dragging ? 'drop-zone-active' : ''} ${file ? 'drop-zone-selected' : ''}`}
          onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click() }}
        >
          <input
            ref={inputRef}
            type="file"
            accept={options.accepted_extensions.join(',')}
            hidden
            onChange={(event) => chooseFile(event.target.files?.[0])}
          />
          {file ? <>
            <div className="drop-file-icon">{fileIcon(file.name)}</div>
            <strong>{file.name}</strong>
            <span>{formatBytes(file.size)} · {imageSelected ? '图片描述必需' : '准备上传'}</span>
            <button type="button" className="file-remove" onClick={(event) => { event.stopPropagation(); clearFile() }} aria-label="移除已选文件"><X size={15} /></button>
          </> : <>
            <div className="drop-icon"><CloudUpload size={25} /></div>
            <strong>将资料拖到此处</strong>
            <span>{formatExtensions(options.accepted_extensions)}</span>
          </>}
        </div>
        <div className="ingestion-fields">
          <label className="field">
            <span>目标 Collection</span>
            <select value={collection} onChange={(event) => setCollection(event.target.value)}>
              {options.collections.map((item) => <option value={item} key={item}>{item}</option>)}
            </select>
          </label>
          <div className="switch-field">
            <div><strong>AI 富化</strong><span>生成元数据并优化文本 Chunk。</span></div>
            <Toggle checked={aiEnrichment} onChange={setAiEnrichment} label="启用 AI 富化" />
          </div>
          <div className="switch-field">
            <div><strong>强制重建索引</strong><span>即便源文件哈希已存在也重新处理。</span></div>
            <Toggle checked={force} onChange={setForce} label="强制重建索引" />
          </div>
        </div>
        <Button type="submit" className="upload-submit" disabled={!file || submitting}>
          {submitting ? <><RefreshCw size={17} className="spin" /> 正在启动…</> : <><UploadCloud size={17} /> 启动入库</>}
        </Button>
      </form>
      <div className="card job-card">
        <SectionHeading eyebrow="流水线活动" title="当前任务" />
        {job ? <JobProgress job={job} progress={progress} pollError={pollError} /> : <EmptyState icon={<HardDrive size={23} />} title="暂无活动任务" description="发起一次入库后，可在此查看实时流水线进度。" />}
      </div>
    </section>
    <section className="card document-manager">
      <SectionHeading eyebrow="索引生命周期" title="文档管理" action={<Button className="button-secondary" onClick={loadDocuments}><RefreshCw size={15} /> 同步</Button>} />
      {documents && documents.documents.length ? <div className="managed-documents">
        {documents.documents.map((document) => <div className="managed-document" key={document.doc_id}>
          <div className="managed-document-icon"><FileText size={18} /></div>
          <div className="managed-document-main"><strong>{document.title || document.source_path.split('/').pop() || document.doc_id}</strong><span>{compactPath(document.source_path)}</span></div>
          <Badge tone="info">{String(document.metadata.collection ?? 'default')}</Badge>
          <div className="managed-document-stats"><span>{formatNumber(Number(document.metadata.chunk_count ?? 0))} 个 Chunk</span><span>{formatDate(document.metadata.updated_at as string | undefined)}</span></div>
          <IconButton className="danger-button" onClick={() => removeDocument(document.doc_id)} aria-label={`删除 ${document.title || document.doc_id}`}><Trash2 size={16} /></IconButton>
        </div>)}
      </div> : <EmptyState icon={<FolderOpen size={23} />} title="索引为空" description="成功入库的文档将在此处展示，并提供生命周期管理操作。" />}
    </section>
  </div>
}

function Capability({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="ingestion-capability"><span>{icon}{label}</span><strong title={value}>{value}</strong></div>
}

function JobProgress({ job, progress, pollError }: { job: IngestionJob; progress: number; pollError: string }) {
  const isError = job.status === 'failed'
  const isDone = ['success', 'skipped'].includes(job.status)
  const statusLabel: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    success: '已完成',
    skipped: '已跳过',
    failed: '失败',
  }
  return <div className="job-progress">
    <div className="job-progress-top">
      <div className={`job-status-icon ${isError ? 'job-status-error' : isDone ? 'job-status-success' : 'job-status-active'}`}>
        {isError ? <CircleAlert size={22} /> : isDone ? <Check size={22} /> : <RefreshCw size={21} className="spin" />}
      </div>
      <div><strong>{isError ? '入库失败' : isDone ? (statusLabel[job.status] ?? `入库${job.status}`) : job.status === 'queued' ? '等待启动' : '正在构建索引'}</strong><span>{job.source_path.split('/').pop()} · {job.collection}</span></div>
      <Badge tone={isError ? 'danger' : isDone ? 'success' : 'info'}>{statusLabel[job.status] ?? job.status}</Badge>
    </div>
    <div className="job-stage"><div><span>当前阶段</span><strong>{job.stage || '准备流水线'}</strong></div><span>{job.total > 0 ? `${job.step} / ${job.total}` : '处理中'}</span></div>
    <ProgressBar value={isDone ? 100 : progress} tone={isDone ? 'success' : 'accent'} />
    {job.error ? <div className="job-error"><CircleAlert size={15} />{job.error}</div> : null}
    {pollError ? <div className="job-error"><CircleAlert size={15} />进度同步失败：{pollError}。正在自动重试。</div> : null}
  </div>
}

function fileIcon(name: string) {
  const extension = fileExtension(name)
  if (['.png', '.jpg', '.jpeg', '.webp'].includes(extension)) return <ImageIcon size={24} />
  if (extension === '.csv') return <FileSpreadsheet size={24} />
  if (extension === '.docx') return <FileType2 size={24} />
  return <FileText size={24} />
}

function isImageFile(name: string): boolean {
  return ['.png', '.jpg', '.jpeg', '.webp'].includes(fileExtension(name))
}

function fileExtension(name: string): string {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index).toLowerCase() : ''
}

function formatExtensions(extensions: readonly string[]): string {
  return extensions.map((extension) => extension.slice(1).toUpperCase()).join(' · ')
}

function formatProvider(provider: string | null, model: string | null): string {
  if (!provider) return '未配置'
  return model ? `${provider} / ${model}` : provider
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
