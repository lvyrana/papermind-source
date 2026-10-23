import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { apiGet, apiPost } from '../api'
import { useAccount } from '../accountContext'
import { createTaskRunner } from '../utils/taskRunner'

export default function PendingTasks() {
  const account = useAccount()
  const [tasks, setTasks] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const runnerRef = useRef(null)
  useEffect(() => {
    if (!account?.authenticated) return
    let poll
    const runner = createTaskRunner({
      list: () => apiGet('/tasks'),
      run: id => apiPost(`/tasks/${id}/run`, {}, { timeoutMs: 55000 }),
      changed: next => {
        setTasks(next)
        window.clearTimeout(poll)
        if (next.some(task => task.state === 'running')) poll = window.setTimeout(() => runner.wake(), 5000)
      },
      failed: setError,
      completed: task => {
        setNotice(task.kind === 'paper-enrich' ? '文献解读已保存，重新打开文献可查看。' : '研究画像记忆已保存。')
        window.dispatchEvent(new CustomEvent('papermind-task-completed', { detail: task }))
      },
    })
    runnerRef.current = runner
    runner.wake()
    const wake = () => runner.wake()
    window.addEventListener('papermind-tasks-changed', wake)
    return () => { runner.stop(); window.clearTimeout(poll); runnerRef.current = null; window.removeEventListener('papermind-tasks-changed', wake) }
  }, [account?.authenticated, account?.account?.id])
  const failed = tasks.filter(task => ['failed', 'interrupted'].includes(task.state))
  const active = tasks.some(task => ['queued', 'running'].includes(task.state))
  if (!active && !failed.length && !error && !notice) return null
  return createPortal(<aside aria-label="解读任务" className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl bg-warm-white border border-cream-dark shadow-md p-3 text-sm text-navy">
    {active && <p role="status">正在补充解读，文献可继续阅读。</p>}
    {failed.map(task => <div key={task.id} className="flex gap-3 items-center">
      <p>{task.error || '解读未完成。'}</p>
      {task.attempts < 3 && <button className="underline whitespace-nowrap" onClick={async () => {
        try { setError(''); await apiPost(`/tasks/${task.id}/retry`, {}); runnerRef.current?.wake() }
        catch { setError('重试未提交，请稍后再试。') }
      }}>重试</button>}
    </div>)}
    {error && <p role="alert">{error}</p>}
    {notice && <div className="flex gap-3"><p role="status">{notice}</p><button aria-label="关闭完成提示" onClick={() => setNotice('')}>关闭</button></div>}
  </aside>, document.body)
}
