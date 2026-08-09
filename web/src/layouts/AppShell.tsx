import {
  Activity,
  BarChart3,
  BookOpen,
  Boxes,
  ChevronRight,
  FileSearch,
  GitBranch,
  Layers3,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  UploadCloud,
  X,
  Zap,
} from 'lucide-react'
import { useEffect, useState, type MouseEvent, type ReactNode } from 'react'
import { api } from '../lib/api'
import { IconButton } from '../components/ui'

const navigation = [
  { to: '/', label: '总览', icon: BarChart3 },
  { to: '/data-browser', label: '数据浏览', icon: BookOpen },
  { to: '/query', label: '知识检索', icon: Search },
  { to: '/ingestion', label: '入库管理', icon: UploadCloud },
  { to: '/traces/ingestion', label: '入库追踪', icon: GitBranch },
  { to: '/traces/query', label: '查询追踪', icon: Activity },
  { to: '/evaluation', label: '评估', icon: Zap },
]

const pageNames: Record<string, string> = {
  '/': '总览',
  '/data-browser': '数据浏览',
  '/query': '知识检索',
  '/ingestion': '入库管理',
  '/traces/ingestion': '入库追踪',
  '/traces/query': '查询追踪',
  '/evaluation': '评估',
}

const healthLabels: Record<'connected' | 'offline' | 'checking', string> = {
  connected: 'API 已连接',
  offline: 'API 不可用',
  checking: '正在检查 API',
}

const liveStatusLabels: Record<'connected' | 'offline' | 'checking', string> = {
  connected: '运行中',
  offline: '已离线',
  checking: '同步中',
}

export function AppShell({ path, onNavigate, children }: { path: string; onNavigate: (path: string) => void; children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [health, setHealth] = useState<'connected' | 'offline' | 'checking'>('checking')
  const pageName = pageNames[path] ?? '工作区'

  const navigate = (event: MouseEvent<HTMLAnchorElement>, nextPath: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    onNavigate(nextPath)
    setMobileOpen(false)
  }

  useEffect(() => {
    let cancelled = false
    let timer: number
    const check = async () => {
      try {
        await api.health()
        if (!cancelled) setHealth('connected')
      } catch {
        if (!cancelled) setHealth('offline')
      } finally {
        if (!cancelled) timer = window.setTimeout(check, 5000)
      }
    }
    void check()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [])

  return <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <aside className={`sidebar ${mobileOpen ? 'sidebar-mobile-open' : ''}`}>
      <div className="brand"><div className="brand-mark"><Layers3 size={21} /></div><div className="brand-copy"><strong>Ragflow</strong><span>运维控制台</span></div><IconButton className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="关闭导航"><X size={18} /></IconButton></div>
      <div className="nav-label">工作区</div>
       <nav className="main-nav">{navigation.map(({ to, label, icon: Icon }) => <a key={to} href={to} onClick={(event) => navigate(event, to)} className={`nav-item ${path === to ? 'nav-item-active' : ''}`}><Icon size={18} /><span>{label}</span></a>)}</nav>
      <div className="sidebar-bottom"><div className="nav-label">系统</div><div className="system-health"><span className={`health-dot health-${health}`} /><span>{healthLabels[health]}</span></div><button className="collapse-button" onClick={() => setCollapsed(!collapsed)}>{collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}<span>{collapsed ? '展开' : '折叠'}侧边栏</span></button></div>
    </aside>
    {mobileOpen ? <button className="sidebar-scrim" onClick={() => setMobileOpen(false)} aria-label="关闭导航" /> : null}
     <div className="main-area"><header className="topbar"><div className="topbar-left"><IconButton className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="打开导航"><Menu size={20} /></IconButton><div className="breadcrumb"><span>工作区</span><ChevronRight size={14} /><strong>{pageName}</strong></div></div><div className="topbar-actions"><div className="live-status"><span className={`health-dot health-${health}`} />{liveStatusLabels[health]}</div></div></header><main className="page-content">{children}</main></div>
  </div>
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return <div className="page-header"><div><span className="eyebrow">{eyebrow ?? '工作区'}</span><h1>{title}</h1>{description ? <p>{description}</p> : null}</div>{actions ? <div className="page-actions">{actions}</div> : null}</div>
}

export function SectionHeading({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return <div className="section-heading"><div>{eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}<h2>{title}</h2>{description ? <p>{description}</p> : null}</div>{action}</div>
}

export function PanelIcon({ children }: { children: ReactNode }) {
  return <div className="panel-icon">{children}</div>
}

export function DataIcon() {
  return <Boxes size={18} />
}

export function DetailIcon() {
  return <FileSearch size={18} />
}
