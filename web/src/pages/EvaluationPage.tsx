import {
  BarChart3,
  CheckCircle2,
  Database,
  FlaskConical,
  Image,
  Play,
  RefreshCw,
  Target,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getErrorMessage, titleCase } from '../lib/format'
import type { HotpotQABenchmarkReport, HotpotQABenchmarkSummary, JsonValue } from '../lib/types'
import { PageHeader, SectionHeading } from '../layouts/AppShell'
import { Badge, Button, EmptyState, ErrorState, LoadingState, Toggle, useToast } from '../components/ui'

export function EvaluationPage() {
  const [summary, setSummary] = useState<HotpotQABenchmarkSummary | null>(null)
  const [report, setReport] = useState<HotpotQABenchmarkReport | null>(null)
  const [includeImages, setIncludeImages] = useState(true)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const { showToast } = useToast()

  const loadSummary = () => {
    setLoading(true)
    setError('')
    api.getHotpotQABenchmark().then(setSummary).catch((reason: unknown) => setError(getErrorMessage(reason))).finally(() => setLoading(false))
  }

  useEffect(() => { loadSummary() }, [])

  const run = async () => {
    setRunning(true)
    try {
      const nextReport = await api.runHotpotQABenchmark(includeImages)
      setReport(nextReport)
      showToast(nextReport.passed ? 'Benchmark 已通过质量门槛' : 'Benchmark 未通过质量门槛', nextReport.passed ? 'success' : 'error')
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    } finally {
      setRunning(false)
    }
  }

  if (loading && !summary) return <><PageHeader eyebrow="质量实验室" title="评估" /><LoadingState label="正在读取 Benchmark 数据" /></>
  if (error && !summary) return <><PageHeader eyebrow="质量实验室" title="评估" /><ErrorState message={error} onRetry={loadSummary} /></>
  if (!summary) return null

  return <div className="page-stack">
    <PageHeader eyebrow="质量实验室" title="评估" description="使用固定 HotpotQA Benchmark 对当前检索配置进行可复现对比。" actions={<Button className="button-secondary" onClick={loadSummary}><RefreshCw size={16} /> 刷新数据</Button>} />
    <section className="evaluation-layout">
      <div className="card evaluation-config"><SectionHeading eyebrow="运行配置" title="HotpotQA Retrieval Benchmark" /><div className="benchmark-facts"><div><span>查询</span><strong>{summary.query_count}</strong></div><div><span>语料段落</span><strong>{summary.corpus_count}</strong></div><div><span>数据状态</span><Badge tone={summary.available ? 'success' : 'danger'}>{summary.available ? '可运行' : '缺失'}</Badge></div></div><div className="evaluation-form"><div className="switch-field"><div><strong>图片召回用例</strong><span>同时执行 3 条原图返回检查。</span></div><Toggle checked={includeImages} onChange={setIncludeImages} label="包含图片召回用例" /></div><div className="evaluation-note"><FlaskConical size={18} /><div><strong>当前保存配置</strong><span>Benchmark 使用本地覆盖配置和独立临时索引，并调用已配置的 Embedding Provider。</span></div></div><Button className="run-button" onClick={run} disabled={running || !summary.available}>{running ? <><RefreshCw className="spin" size={17} /> Benchmark 运行中</> : <><Play size={17} fill="currentColor" /> 运行 Benchmark</>}</Button></div></div>
      <div className="card evaluation-side"><SectionHeading eyebrow="评测范围" title="固定实验条件" /><div className="quality-signal"><div className="quality-signal-icon quality-indigo"><Database size={17} /></div><div><strong>隔离索引</strong><span>不读写 Dashboard 业务 Collection。</span></div></div><div className="quality-signal"><div className="quality-signal-icon quality-cyan"><Target size={17} /></div><div><strong>统一截断</strong><span>BM25、Dense、Hybrid 均计算 Hit@5 与 MRR@5。</span></div></div><div className="quality-signal"><div className="quality-signal-icon quality-violet"><Image size={17} /></div><div><strong>原图检查</strong><span>验证命中 Chunk 的关联图片能够返回。</span></div></div></div>
    </section>
    <BenchmarkResults report={report} />
  </div>
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
