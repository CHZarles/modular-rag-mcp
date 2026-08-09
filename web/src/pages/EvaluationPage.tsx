import {
  BarChart3,
  CheckCircle2,
  CircleAlert,
  Database,
  Image,
  Play,
  RefreshCw,
  Target,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatDate, formatElapsedSeconds, getErrorMessage, titleCase } from '../lib/format'
import type { HotpotQABenchmarkJob, HotpotQABenchmarkReport, HotpotQABenchmarkSummary, JsonValue } from '../lib/types'
import { PageHeader, SectionHeading } from '../layouts/AppShell'
import { Badge, Button, EmptyState, ErrorState, LoadingState, Toggle, useToast } from '../components/ui'

export function EvaluationPage() {
  const [summary, setSummary] = useState<HotpotQABenchmarkSummary | null>(null)
  const [job, setJob] = useState<HotpotQABenchmarkJob | null>(null)
  const [report, setReport] = useState<HotpotQABenchmarkReport | null>(null)
  const [includeImages, setIncludeImages] = useState(true)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [observedAt, setObservedAt] = useState(0)
  const { showToast } = useToast()

  const loadBenchmark = () => {
    setLoading(true)
    setError('')
    Promise.all([api.getHotpotQABenchmark(), api.getHotpotQABenchmarkRun()]).then(([nextSummary, nextJob]) => {
      setSummary(nextSummary)
      setJob(nextJob)
      setObservedAt(Date.now() / 1000)
      if (nextJob) setIncludeImages(nextJob.include_images)
      if (nextJob?.report) setReport(nextJob.report)
    }).catch((reason: unknown) => setError(getErrorMessage(reason))).finally(() => setLoading(false))
  }

  useEffect(() => { loadBenchmark() }, [])

  useEffect(() => {
    if (!job?.active) return
    let cancelled = false
    let timer: number
    const poll = async () => {
      try {
        const nextJob = await api.getHotpotQABenchmarkRun()
        if (cancelled || !nextJob) return
        setJob(nextJob)
        setObservedAt(Date.now() / 1000)
        setPollError('')
        if (nextJob.report) {
          setReport(nextJob.report)
          showToast(nextJob.report.passed ? 'Benchmark 已通过质量门槛' : 'Benchmark 未通过质量门槛', nextJob.report.passed ? 'success' : 'error')
        } else if (nextJob.active) {
          timer = window.setTimeout(poll, 1000)
        }
      } catch (reason) {
        if (cancelled) return
        setPollError(getErrorMessage(reason))
        timer = window.setTimeout(poll, 3000)
      }
    }
    timer = window.setTimeout(poll, 800)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [job?.active, showToast])

  const run = async () => {
    setSubmitting(true)
    try {
      const nextJob = await api.runHotpotQABenchmark(includeImages)
      setJob(nextJob)
      setObservedAt(Date.now() / 1000)
      setReport(null)
      setPollError('')
      showToast('Benchmark 已在后台启动', 'success')
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading && !summary) return <><PageHeader eyebrow="质量实验室" title="评估" /><LoadingState label="正在读取 Benchmark 数据" /></>
  if (error && !summary) return <><PageHeader eyebrow="质量实验室" title="评估" /><ErrorState message={error} onRetry={loadBenchmark} /></>
  if (!summary) return null

  return <div className="page-stack">
    <PageHeader eyebrow="质量实验室" title="评估" actions={<Button className="button-secondary" onClick={loadBenchmark}><RefreshCw size={16} /> 刷新状态</Button>} />
    <section className="evaluation-layout">
      <div className="card evaluation-config"><SectionHeading eyebrow="运行配置" title="HotpotQA Retrieval Benchmark" /><div className="benchmark-facts"><div><span>查询</span><strong>{summary.query_count}</strong></div><div><span>语料段落</span><strong>{summary.corpus_count}</strong></div><div><span>数据状态</span><Badge tone={summary.available ? 'success' : 'danger'}>{summary.available ? '可运行' : '缺失'}</Badge></div></div><BenchmarkRunStatus job={job} pollError={pollError} observedAt={observedAt} /><div className="evaluation-form"><div className="switch-field"><div><strong>图片召回用例</strong><span>同时执行 3 条原图返回检查。</span></div><Toggle checked={includeImages} onChange={setIncludeImages} label="包含图片召回用例" /></div><Button className="run-button" onClick={run} disabled={submitting || job?.active || !summary.available}>{submitting || job?.active ? <><RefreshCw className="spin" size={17} /> Benchmark 运行中</> : <><Play size={17} fill="currentColor" /> 运行 Benchmark</>}</Button></div></div>
      <div className="card evaluation-side"><SectionHeading eyebrow="评测范围" title="固定实验条件" /><div className="quality-signal"><div className="quality-signal-icon quality-indigo"><Database size={17} /></div><div><strong>隔离索引</strong></div></div><div className="quality-signal"><div className="quality-signal-icon quality-cyan"><Target size={17} /></div><div><strong>Hit@5 / MRR@5</strong></div></div><div className="quality-signal"><div className="quality-signal-icon quality-violet"><Image size={17} /></div><div><strong>图片检查</strong></div></div></div>
    </section>
    <BenchmarkResults report={report} />
  </div>
}

function BenchmarkRunStatus({ job, pollError, observedAt }: { job: HotpotQABenchmarkJob | null; pollError: string; observedAt: number }) {
  if (!job) return null
  const failed = job.status === 'failed'
  const completed = job.status === 'success'
  const now = job.finished_at ?? observedAt
  const elapsed = job.started_at === null || now === 0 ? null : formatElapsedSeconds(now - job.started_at)
  const label = failed ? '运行失败' : completed ? '运行完成' : job.status === 'queued' ? '等待启动' : '正在运行'
  return <div className="benchmark-run-status" aria-live="polite"><div className={`job-status-icon ${failed ? 'job-status-error' : completed ? 'job-status-success' : 'job-status-active'}`}>{failed ? <CircleAlert size={21} /> : completed ? <CheckCircle2 size={21} /> : <RefreshCw className="spin" size={20} />}</div><div><strong>{label}</strong><span>{job.include_images ? '包含图片用例' : '仅文本用例'} · {job.started_at ? `开始于 ${formatDate(job.started_at)}` : '已进入执行队列'}{elapsed ? ` · 已用时 ${elapsed}` : ''}</span></div><Badge tone={failed ? 'danger' : completed ? 'success' : 'info'}>{job.status}</Badge>{job.error ? <div className="job-error"><CircleAlert size={15} />{job.error}</div> : null}{pollError ? <div className="job-error"><CircleAlert size={15} />状态同步失败：{pollError}。正在自动重试。</div> : null}</div>
}

function BenchmarkResults({ report }: { report: HotpotQABenchmarkReport | null }) {
  if (!report) return <section className="card evaluation-empty"><EmptyState icon={<BarChart3 size={25} />} title="暂无 Benchmark 结果" description="尚未执行本次配置评测。" /></section>
  const strategies = Object.entries(report.strategies)
  return <section className="evaluation-results"><div className="results-heading"><div><span className="eyebrow">运行结果</span><h2>HotpotQA 策略对比</h2><p>{report.chunk_count} 个 Chunk · {report.embedding.provider}/{report.embedding.model ?? 'default'} · {report.embedding.dimension} 维</p></div><Badge tone={report.passed ? 'success' : 'danger'}>{report.passed ? '质量门槛通过' : '质量门槛未通过'}</Badge></div><div className="benchmark-strategy-grid">{strategies.map(([name, metrics]) => <article className={`card benchmark-strategy ${name === report.gate.strategy ? 'benchmark-selected' : ''}`} key={name}><div className="benchmark-strategy-heading"><div><span>{titleCase(name)}</span>{name === report.gate.strategy ? <Badge tone="info">当前策略</Badge> : null}</div><strong>{metrics.case_count} 条查询</strong></div><div className="benchmark-metrics"><div><span>Hit@5</span><strong>{percent(metrics.hit_at_5)}</strong></div><div><span>MRR@5</span><strong>{percent(metrics.mrr_at_5)}</strong></div></div><div className="benchmark-misses"><span>未命中</span><strong>{metrics.misses.length}</strong></div>{metrics.misses.length ? <details><summary>查看失败查询</summary><ul>{metrics.misses.map((query) => <li key={query}>{query}</li>)}</ul></details> : <div className="benchmark-complete"><CheckCircle2 size={14} /> 全部查询已命中</div>}</article>)}</div><div className="benchmark-bottom-grid"><article className="card benchmark-image-result"><SectionHeading eyebrow="多模态" title="图片召回" /><div className="benchmark-metrics"><div><span>Hit@5</span><strong>{percent(report.image_cases.hit_at_5)}</strong></div><div><span>MRR@5</span><strong>{percent(report.image_cases.mrr_at_5)}</strong></div></div><span>{report.image_cases.case_count} 条图片用例</span></article><article className="card benchmark-config-result"><SectionHeading eyebrow="可复现性" title="本次参数快照" /><dl><div><dt>Embedding</dt><dd>{report.embedding.provider}/{report.embedding.model ?? 'default'}</dd></div>{Object.entries(report.retrieval).map(([key, value]) => <div key={key}><dt>{titleCase(key)}</dt><dd>{formatValue(value)}</dd></div>)}</dl></article></div></section>
}

function percent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatValue(value: JsonValue): string {
  if (value === null) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
