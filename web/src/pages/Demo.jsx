import { useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, BookmarkPlus, Check, Download, Layers, Quote, RotateCcw } from 'lucide-react'
import PublicLayout from '../components/PublicLayout'
import { DEMO_STORAGE_KEY, demoMarkdown, demoPaper, readDemoCards } from '../data/demoPaper'

export default function Demo() {
  const [params, setParams] = useSearchParams()
  const section = demoPaper.sections.find(item => item.id === params.get('section')) || demoPaper.sections[0]
  const [saved, setSaved] = useState(readDemoCards)
  const [storageUnavailable, setStorageUnavailable] = useState(false)
  const [notice, setNotice] = useState('')
  const [highlighted, setHighlighted] = useState(false)
  const [prompt, setPrompt] = useState(null)
  const sourceRef = useRef(null)
  const currentSaved = saved.includes(section.id)

  function selectSection(id) {
    setParams({ section: id }, { replace: true })
    setHighlighted(false)
    setPrompt(null)
    setNotice('')
  }

  function returnToSource() {
    setHighlighted(true)
    sourceRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' })
    sourceRef.current?.focus({ preventScroll: true })
    setNotice(`已定位：${section.source}`)
  }

  function saveCard() {
    const next = currentSaved ? saved : [...saved, section.id]
    setSaved(next)
    try {
      localStorage.setItem(DEMO_STORAGE_KEY, JSON.stringify(next))
      setStorageUnavailable(false)
      setNotice('已保存到本机样例卡片。')
    } catch {
      setStorageUnavailable(true)
      setNotice('浏览器禁止本地存储，本次卡片仅在当前页面保留。可立即下载 Markdown。')
    }
  }

  function clearCards() {
    try { localStorage.removeItem(DEMO_STORAGE_KEY) } catch {
      setNotice('浏览器禁止清除存储；请在浏览器设置中清除本站样例数据。')
      return
    }
    setSaved([])
    setNotice('本机样例卡片已清空。')
  }

  function downloadCards() {
    const url = URL.createObjectURL(new Blob([demoMarkdown(saved)], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'litfinch-demo-reading-cards.md'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    setNotice('已下载样例卡片，包含论文出处与原文节选。')
  }

  return <PublicLayout title="免费试读一篇" demo>
    <main id="main-content" className="lf-container lf-demo-main">
      <div className="lf-demo-topline"><Link to="/welcome"><ArrowLeft size={14} />产品首页</Link><span><span className="lf-status-dot" />免费试读 · 预设解读</span></div>
      <header className="lf-demo-title"><p className="lf-eyebrow">A SMALL READING, A REAL TAKEAWAY</p><h1>跟着三个问题，读深一篇。</h1><p>点开解读、核对原文，再留下一张阅读卡片。</p></header>
      <nav className="lf-demo-steps" aria-label="试读问题">
        {demoPaper.sections.map(item => <button key={item.id} type="button" aria-pressed={item.id === section.id} onClick={() => selectSection(item.id)}>
          <span className="lf-step-index">{saved.includes(item.id) ? <Check size={16} /> : item.number}</span><span>{item.label}</span><span className="lf-demo-step-description">{item.kind}</span>
        </button>)}
      </nav>

      <div className="lf-demo-grid">
        <article className="lf-source-panel" aria-label="论文原文节选">
          <div className="lf-panel-top"><span><BookOpen size={15} />原文节选</span><span>PLOS ONE · 2022</span></div>
          <div className="lf-source-content"><p className="lf-source-category">RESEARCH ARTICLE · OPEN ACCESS</p><h2>{demoPaper.title}</h2><p className="lf-source-authors">{demoPaper.authors}</p><p className="lf-source-zh">{demoPaper.titleZh}</p>
            <div className="lf-source-location"><span>{section.source}</span><span>英文原文</span></div>
            <blockquote ref={sourceRef} tabIndex={-1} className={highlighted ? 'lf-source-quote lf-source-highlight' : 'lf-source-quote'}>
              {section.quote}
            </blockquote>
            {highlighted && <p className="lf-evidence-found"><Check size={14} />已定位到这条解读的原文</p>}
            <details className="lf-translation" key={section.id}><summary>查看中文译文</summary><p>{section.translation}</p><small>研雀整理的示例译文</small></details>
            <div className="lf-source-context"><span>阅读上下文</span><p>{section.context}</p></div>
            <a className="lf-text-link" href={demoPaper.url} target="_blank" rel="noreferrer">查看出版方全文 <ArrowUpRight size={15} /></a>
          </div>
          <div className="lf-source-license">© 2022 Jeganathan et al. · <a href={demoPaper.license} target="_blank" rel="noreferrer">CC BY 4.0</a><br />原文节选重新排版；中文翻译、解读和追问为研雀示例。</div>
        </article>

        <section className="lf-reading-panel" aria-label="精读与卡片">
          <div className="lf-panel-top"><span><Quote size={15} />精读工作台</span><span>预设解读示例</span></div>
          <div className="lf-reading-content"><p className="lf-reading-label">{section.number} / {section.label}</p><h2>{section.question}</h2><h3>{section.lead}</h3><p className="lf-explanation">{section.explanation}</p>
            <button type="button" className="lf-evidence-button" onClick={returnToSource}><Quote size={14} />回到原文核对 <ArrowUpRight size={14} /></button>
            <div className="lf-prompt-box"><span className="lf-tiny-label">继续追问 · 预设问答</span><div>{section.prompts.map((item, index) => <button type="button" key={item.question} aria-expanded={prompt === index} onClick={() => setPrompt(prompt === index ? null : index)}>{item.question}<span>{prompt === index ? '−' : '+'}</span></button>)}</div>{prompt !== null && <p className="lf-prompt-answer" role="status">{section.prompts[prompt]?.answer}</p>}</div>
            <div className="lf-demo-card"><span className="lf-card-label"><Layers size={14} />{section.kind}<span>卡片预览</span></span><p>{section.takeaway}</p><button className="lf-anchor-link" type="button" onClick={returnToSource}><Quote size={12} />{section.source}<ArrowUpRight size={13} /></button></div>
            <button type="button" className="lf-button lf-save-button" onClick={saveCard}>{currentSaved ? <Check size={16} /> : <BookmarkPlus size={16} />}{currentSaved ? '已存入本机样例卡片' : '保存这张样例卡片'}</button>
            {storageUnavailable && <p className="lf-save-note">本地存储不可用，建议下载卡片。</p>}
          </div>
        </section>
      </div>
      <p role="status" className="lf-demo-notice" aria-live="polite">{notice}</p>

      <section className="lf-demo-collection" aria-label="本机样例卡片">
        <div className="lf-collection-heading"><div><span className="lf-eyebrow">A TAKEAWAY TO KEEP</span><h2>本机样例卡片 <span>{saved.length} / 3</span></h2></div><div className="lf-collection-actions"><button type="button" disabled={saved.length === 0} className="lf-text-link" onClick={clearCards}><RotateCcw size={14} />清空样例</button><button type="button" disabled={saved.length === 0} className="lf-button lf-button-outline lf-button-small" onClick={downloadCards}><Download size={15} />下载 Markdown</button></div></div>
        {saved.length > 0 ? <div className="lf-saved-cards">{demoPaper.sections.filter(item => saved.includes(item.id)).map(item => <button key={item.id} type="button" onClick={() => { selectSection(item.id); window.scrollTo({ top: 0, behavior: 'instant' }) }}><span><Check size={13} />{item.kind}</span><p>{item.takeaway}</p><small>{item.source}<ArrowUpRight size={13} /></small></button>)}</div> : <p className="lf-collection-empty">保存上方卡片后，可以在这里回看并带走原文出处。</p>}
      </section>
      <section className="lf-demo-next"><div><p className="lf-eyebrow">THE NEXT PAPER IS YOURS</p><h2>{saved.length === 3 ? '三张卡片，一次完整的试读。' : '换成一篇真正想读的论文。'}</h2><p>进入工作台上传 PDF，或连接 Zotero 开始精读。</p></div><div><Link to="/" className="lf-button">打开自己的论文 <ArrowRight size={16} /></Link><Link to="/login" className="lf-text-link">连接 Zotero 账号 <ArrowUpRight size={15} /></Link></div></section>
    </main>
  </PublicLayout>
}
