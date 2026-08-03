import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Component, type ReactNode } from 'react'
import { Button } from './ui'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  message: string
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message }
  }

  reset = () => {
    this.setState({ hasError: false, message: '' })
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return <main className="fatal-error"><div className="fatal-error-icon"><AlertTriangle size={27} /></div><span className="eyebrow">工作区异常</span><h1>控制台出现了意外中断</h1><p>{this.state.message || '页面无法正常渲染，请稍后重试。'}</p><Button onClick={this.reset}><RefreshCw size={16} /> 重新加载</Button></main>
  }
}
