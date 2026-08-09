import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastProvider } from './components/ui'
import { AppShell } from './layouts/AppShell'
import { DataBrowserPage } from './pages/DataBrowserPage'
import { EvaluationPage } from './pages/EvaluationPage'
import { IngestionPage } from './pages/IngestionPage'
import { IngestionTracesPage, QueryTracesPage } from './pages/TracesPage'
import { OverviewPage } from './pages/OverviewPage'
import { QueryPage } from './pages/QueryPage'
import './styles.css'

const routes: Record<string, ReactNode> = {
  '/': <OverviewPage />,
  '/data-browser': <DataBrowserPage />,
  '/query': <QueryPage />,
  '/ingestion': <IngestionPage />,
  '/traces/ingestion': <IngestionTracesPage />,
  '/traces/query': <QueryTracesPage />,
  '/evaluation': <EvaluationPage />,
}

export default function App() {
  const [path, setPath] = useState(window.location.pathname)

  useEffect(() => {
    const handlePopState = () => setPath(window.location.pathname)
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const navigate = useCallback((nextPath: string) => {
    if (nextPath === window.location.pathname) return
    window.history.pushState({}, '', nextPath)
    setPath(nextPath)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const currentPath = routes[path] ? path : '/'
  return <ErrorBoundary><ToastProvider><AppShell path={currentPath} onNavigate={navigate}>{routes[currentPath]}</AppShell></ToastProvider></ErrorBoundary>
}
