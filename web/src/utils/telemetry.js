export function getTelemetryConfig(env = {}) {
  const dsn = (env.VITE_SENTRY_DSN || '').trim()
  if (!dsn) return null

  const replayEnabled = env.VITE_SENTRY_REPLAY_ENABLED === 'true'
  return {
    replayEnabled,
    options: {
      dsn,
      sendDefaultPii: false,
      tracesSampleRate: 0,
      replaysSessionSampleRate: 0,
      replaysOnErrorSampleRate: replayEnabled ? 1 : 0,
    },
  }
}
