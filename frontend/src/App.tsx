import { Suspense, lazy } from 'react'
import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { WorkspacePage } from './pages/WorkspacePage'
import { ProgressPage } from './pages/ProgressPage'
import { ReportPage } from './pages/ReportPage'
import { ComparisonPage } from './pages/ComparisonPage'
import { SettingsPage } from './pages/SettingsPage'
import { EmptyState } from './components/ui/EmptyState'

const DevPreviewPage = import.meta.env.DEV ? lazy(() =>
  import('./pages/DevPreviewPage').then((m) => ({ default: m.DevPreviewPage })),
) : null

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<WorkspacePage />} />
        <Route path="/task/:taskId" element={<ProgressPage />} />
        <Route path="/report/:reportId" element={<ReportPage />} />
        <Route path="/compare" element={<ComparisonPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        {DevPreviewPage && (
          <Route
            path="/dev/preview"
            element={
              <Suspense fallback={null}>
                <DevPreviewPage />
              </Suspense>
            }
          />
        )}
        <Route
          path="*"
          element={
            <EmptyState
              title="Page not found"
              detail="The address does not match any workspace page."
            />
          }
        />
      </Routes>
    </AppShell>
  )
}
