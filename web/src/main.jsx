import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import * as Sentry from '@sentry/react'
import './index.css'
import App from './App.jsx'
import AppErrorFallback from './components/AppErrorFallback.jsx'
import { getTelemetryConfig } from './utils/telemetry.js'

// 自部署默认不向任何 Sentry 项目上报；回放需要单独明确开启。
const telemetry = getTelemetryConfig(import.meta.env)
if (telemetry) {
  Sentry.init({
    ...telemetry.options,
    integrations: telemetry.replayEnabled ? [Sentry.replayIntegration({
      maskAllText: true,
      blockAllMedia: true,
    })] : [],
  })
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Sentry.ErrorBoundary fallback={<AppErrorFallback />}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </Sentry.ErrorBoundary>
  </StrictMode>,
)
