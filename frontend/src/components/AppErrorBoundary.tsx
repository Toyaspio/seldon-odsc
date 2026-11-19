import { Component, type ReactNode } from 'react'

export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <main className="mx-auto max-w-xl px-6 py-16 text-fog-100">
        <section className="panel space-y-4 p-6" role="alert">
          <h1 className="text-xl font-semibold">This page could not be displayed</h1>
          <p className="text-sm text-fog-300">
            Reload this page to reconnect to the same task or report.
          </p>
          <button type="button" className="btn-primary" onClick={() => window.location.reload()}>
            Reload this page
          </button>
          <a className="btn-ghost ml-2" href="/">Back to workspace</a>
        </section>
      </main>
    )
  }
}
