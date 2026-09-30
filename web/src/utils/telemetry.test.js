import test from 'node:test'
import assert from 'node:assert/strict'
import { getTelemetryConfig } from './telemetry.js'

test('未配置自有 DSN 时不初始化诊断服务，即使误开回放', () => {
  assert.equal(getTelemetryConfig(), null)
  assert.equal(getTelemetryConfig({ VITE_SENTRY_DSN: '  ' }), null)
  assert.equal(getTelemetryConfig({ VITE_SENTRY_REPLAY_ENABLED: 'true' }), null)
})

test('只配置 DSN 不会开启回放、性能追踪或默认个人信息采集', () => {
  const config = getTelemetryConfig({ VITE_SENTRY_DSN: ' https://public@example.invalid/1 ' })
  assert.equal(config.options.dsn, 'https://public@example.invalid/1')
  assert.equal(config.replayEnabled, false)
  assert.equal(config.options.sendDefaultPii, false)
  assert.equal(config.options.tracesSampleRate, 0)
  assert.equal(config.options.replaysSessionSampleRate, 0)
  assert.equal(config.options.replaysOnErrorSampleRate, 0)
})

test('回放只接受显式 true，不把 false 字符串当作开启', () => {
  for (const value of [undefined, '', 'false', '1', 'TRUE']) {
    assert.equal(getTelemetryConfig({
      VITE_SENTRY_DSN: 'https://public@example.invalid/1',
      VITE_SENTRY_REPLAY_ENABLED: value,
    }).replayEnabled, false)
  }
  const config = getTelemetryConfig({
    VITE_SENTRY_DSN: 'https://public@example.invalid/1',
    VITE_SENTRY_REPLAY_ENABLED: 'true',
  })
  assert.equal(config.replayEnabled, true)
  assert.equal(config.options.replaysOnErrorSampleRate, 1)
  assert.equal(config.options.replaysSessionSampleRate, 0)
})
