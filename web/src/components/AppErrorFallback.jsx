function AppErrorFallback() {
  return (
    <main className="min-h-screen bg-cream px-6 py-16 text-navy">
      <div className="mx-auto max-w-lg rounded-3xl border border-navy/10 bg-warm-white p-8 shadow-[0_18px_60px_rgba(30,58,95,0.12)]">
        <p className="font-serif text-2xl font-semibold">页面没有正常显示</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            className="rounded-full bg-navy px-5 py-2.5 text-sm text-white"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
          <button
            type="button"
            className="rounded-full border border-navy/15 px-5 py-2.5 text-sm"
            onClick={() => window.location.assign('/')}
          >
            返回首页
          </button>
        </div>
      </div>
    </main>
  )
}

export default AppErrorFallback
