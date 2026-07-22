import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Card } from './ui'

/** Root error boundary. Before this, a render crash anywhere in a
 * page left the whole app (including the sidebar) unusable with a
 * blank screen. Now it degrades to a card with the error message and
 * a reload action. */

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled render error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center p-8">
          <Card title="Something went wrong" className="max-w-xl w-full">
            <p className="text-sm text-text-secondary mb-1">
              The Hub hit an unexpected error while rendering. The
              backend is unaffected.
            </p>
            <pre className="text-xs text-danger bg-surface rounded-lg p-3 my-3 overflow-auto max-h-40 whitespace-pre-wrap">
              {this.state.error.message}
            </pre>
            <div className="flex gap-2">
              <Button onClick={() => window.location.reload()}>
                Reload Hub
              </Button>
              <Button
                variant="secondary"
                onClick={() => this.setState({ error: null })}
              >
                Try to continue
              </Button>
            </div>
          </Card>
        </div>
      )
    }
    return this.props.children
  }
}
