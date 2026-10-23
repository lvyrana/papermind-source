import test from 'node:test'
import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers'
import { createTaskRunner } from './taskRunner.js'

test('queued jobs run once, failed/running work does not automatically replay', async () => {
  const calls = []
  let rounds = 0
  const runner = createTaskRunner({
    list: async () => ({ enabled: true, tasks: rounds++ ? [{id:'a',state:'failed',attempts:1}] : [
      {id:'a',state:'queued',attempts:0}, {id:'b',state:'running',attempts:1}, {id:'c',state:'interrupted',attempts:1},
    ] }), run: async id => { calls.push(id); return {ok:false} }, changed() {}, completed() {}, failed() {},
  })
  await runner.wake()
  assert.deepEqual(calls,['a'])
})

test('unknown HTTP result is not silently retried', async () => {
  let count = 0
  let errors = 0
  const runner = createTaskRunner({
    list: async () => ({enabled:true,tasks:[{id:'a',state:'queued',attempts:0}]}),
    run: async () => { count++; throw Error('network') }, changed() {}, completed() {}, failed() { errors++ },
  })
  await runner.wake()
  await runner.wake()
  assert.equal(count,1)
  assert.equal(errors,1)
})

test('leaving the account prevents scheduling more jobs and late UI writes', async () => {
  let finish
  const called = []
  const runner = createTaskRunner({
    list: async () => ({enabled:true,tasks:[{id:'a',state:'queued',attempts:0},{id:'b',state:'queued',attempts:0}]}),
    run: id => { called.push(id); return new Promise(resolve => { finish=resolve }) },
    changed() {}, completed() { assert.fail('stale account UI write') }, failed() {},
  })
  const pending = runner.wake()
  await new Promise(resolve => setImmediate(resolve))
  runner.stop()
  finish({ok:true,task:{id:'a'}})
  await pending
  assert.deepEqual(called,['a'])
})
