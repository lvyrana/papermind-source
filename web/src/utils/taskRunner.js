// A page starts each queued attempt once. Unknown results require refresh or
// explicit retry; no unattended retry loop can multiply model charges.
export function createTaskRunner({ list, run, changed, completed, failed }) {
  let stopped = false
  let working = false
  let requested = false
  const attempted = new Set()
  async function wake() {
    if (stopped) return
    requested = true
    if (working) return
    working = true
    try {
      while (requested && !stopped) {
        requested = false
        const data = await list()
        if (stopped || !data.enabled) break
        changed(data.tasks || [])
        for (const task of data.tasks || []) {
          const key = `${task.id}:${task.attempts}`
          if (stopped) break
          if (task.state !== 'queued' || attempted.has(key)) continue
          attempted.add(key)
          const result = await run(task.id)
          if (stopped) break
          if (result.ok) completed(result.task)
          requested = true
        }
      }
    } catch {
      if (!stopped) failed('解读任务连接中断。刷新可查看状态，已保存的文献不受影响。')
    } finally { working = false }
  }
  return { wake, stop() { stopped = true } }
}
