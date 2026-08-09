import {
  BarChart3,
  Check,
  ChevronDown,
  CircleHelp,
  FlaskConical,
  Play,
  RefreshCw,
  Search,
  Target,
  Trophy,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getErrorMessage, titleCase } from '../lib/format'
import type { EvaluationOptions, EvaluationReport, JsonValue } from '../lib/types'
import { selectAvailableValue } from '../lib/view-model'
import { PageHeader, SectionHeading } from '../layouts/AppShell'
import { Badge, Button, EmptyState, ErrorState, LoadingState, useToast } from '../components/ui'

export function EvaluationPage() {
  const [options, setOptions] = useState<EvaluationOptions | null>(null)
  const [backends, setBackends] = useState<string[]>([])
  const [testSetPath, setTestSetPath] = useState('')
  const [report, setReport] = useState<EvaluationReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const { showToast } = useToast()

  const loadOptions = () => {
    setLoading(true)
    setError('')
    api.getEvaluationOptions().then((nextOptions) => {
      setOptions(nextOptions)
      setBackends((current) => {
        const available = current.filter((backend) => nextOptions.backends.includes(backend))
        return available.length ? available : nextOptions.backends.slice(0, 1)
      })
      setTestSetPath((current) => selectAvailableValue(current, nextOptions.golden_test_sets))
    }).catch((reason: unknown) => setError(getErrorMessage(reason))).finally(() => setLoading(false))
  }

  useEffect(() => { loadOptions() }, [])

  const toggleBackend = (backend: string) => setBackends((current) => current.includes(backend) ? current.filter((item) => item !== backend) : [...current, backend])
  const run = async () => {
    if (!testSetPath || !backends.length) return
    setRunning(true)
    try {
      const nextReport = await api.runEvaluation(testSetPath, backends)
      setReport(nextReport)
      showToast('评估运行已完成', 'success')
    } catch (reason) {
      showToast(getErrorMessage(reason), 'error')
    } finally {
      setRunning(false)
    }
  }

  if (loading && !options) return <><PageHeader eyebrow="质量实验室" title="评估" description="基于黄金测试集评估检索质量，并对比不同评估后端的表现。" /><LoadingState /></>
  if (error && !options) return <><PageHeader eyebrow="质量实验室" title="评估" /><ErrorState message={error} onRetry={loadOptions} /></>
  if (!options) return null
  return <div className="page-stack"><PageHeader eyebrow="质量实验室" title="评估" description="基于黄金测试集评估检索质量，并对比不同评估后端的表现。" actions={<Button className="button-secondary" onClick={loadOptions}><RefreshCw size={16} /> 刷新选项</Button>} /><section className="evaluation-layout"><div className="card evaluation-config"><SectionHeading eyebrow="运行配置" title="规划一次质量评估" description="选择一个或多个评估器，以及用于定义成功标准的黄金测试集。" /><div className="evaluation-form"><div className="field"><span>黄金测试集</span><div className="select-wrap select-wrap-full"><Target size={16} /><select value={testSetPath} onChange={(event) => setTestSetPath(event.target.value)}><option value="">请选择黄金测试集</option>{options.golden_test_sets.map((path) => <option value={path} key={path}>{path}</option>)}</select><ChevronDown size={15} /></div>{!options.golden_test_sets.length ? <small className="field-help"><CircleHelp size={14} /> API 未返回任何黄金测试集。</small> : null}</div><div className="field"><span>评估后端 <small>已选 {backends.length} 个</small></span><div className="backend-list">{options.backends.map((backend) => <button type="button" className={`backend-option ${backends.includes(backend) ? 'backend-option-selected' : ''}`} key={backend} onClick={() => toggleBackend(backend)}><div className="backend-option-icon"><FlaskConical size={17} /></div><div><strong>{titleCase(backend)}</strong><span>{backend === 'custom' ? '基于确定性的检索校验' : '由 Provider 提供的质量信号'}</span></div><span className="backend-check">{backends.includes(backend) ? <Check size={14} /> : null}</span></button>)}</div></div><Button className="run-button" onClick={run} disabled={running || !testSetPath || !backends.length}>{running ? <><RefreshCw className="spin" size={17} /> 评估运行中…</> : <><Play size={17} fill="currentColor" /> 开始评估</>}</Button></div></div><div className="card evaluation-side"><SectionHeading eyebrow="评估维度" title="质量指标" /><div className="quality-signal"><div className="quality-signal-icon quality-indigo"><Target size={17} /></div><div><strong>命中率</strong><span>每个用例命中的相关 Chunk 比例。</span></div></div><div className="quality-signal"><div className="quality-signal-icon quality-cyan"><Search size={17} /></div><div><strong>检索精度</strong><span>在指定截断位置上的信号质量。</span></div></div><div className="quality-signal"><div className="quality-signal-icon quality-violet"><BarChart3 size={17} /></div><div><strong>后端对比</strong><span>同时运行多种评估器进行横向对比。</span></div></div><div className="evaluation-note"><Trophy size={18} /><div><strong>保持评估可复现</strong><span>使用带版本号的黄金测试集，并通过 Run ID 在追踪历史中比对结果。</span></div></div></div></section><Results report={report} /></div>
}

function Results({ report }: { report: EvaluationReport | null }) {
  if (!report) return <section className="card evaluation-empty"><EmptyState icon={<BarChart3 size={25} />} title="暂无评估结果" description="完成上方配置并运行一次评估后，可在此查看聚合指标与用例级结果。" /></section>
  const metrics = Object.entries(report.metrics)
  return <section className="evaluation-results"><div className="results-heading"><div><span className="eyebrow">运行结果</span><h2>评估报告</h2><p>运行 ID <code>{report.run_id}</code> · 共 {String(report.metadata.case_count ?? report.cases.length)} 个用例</p></div><Badge tone="success">已完成</Badge></div><div className="metric-result-grid">{metrics.map(([key, value]) => <div className="metric-result" key={key}><span>{titleCase(key)}</span><strong>{formatMetric(value)}</strong><div className="result-meter"><span style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></div><small>综合得分</small></div>)}</div><div className="card cases-card"><SectionHeading eyebrow="用例浏览器" title="测试用例" description="查看评估服务返回的原始用例负载。" /><div className="cases-table"><div className="cases-table-header"><span>用例</span><span>查询</span><span>结果</span><span>详情</span></div>{report.cases.length ? report.cases.map((item, index) => <div className="case-row" key={String(item.case_id ?? item.id ?? index)}><strong>{String(item.case_id ?? item.id ?? `case-${index + 1}`)}</strong><span>{String(item.query ?? item.question ?? '—')}</span><span><Badge tone={caseSuccessful(item) ? 'success' : 'warning'}>{caseSuccessful(item) ? '通过' : '需复核'}</Badge></span><code>{JSON.stringify(item)}</code></div>) : <EmptyState icon={<CircleHelp size={21} />} title="未返回用例详情" description="评估器只返回了聚合指标，未提供单独的用例信息。" />}</div></div></section>
}

function formatMetric(value: number): string {
  if (value >= 0 && value <= 1) return `${(value * 100).toFixed(1)}%`
  return value.toFixed(2)
}

function caseSuccessful(item: Record<string, JsonValue>): boolean {
  const status = item.status ?? item.result ?? item.passed
  return status === true || status === 'pass' || status === 'passed' || status === 'success'
}
