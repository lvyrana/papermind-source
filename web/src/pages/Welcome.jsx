import { Link } from 'react-router-dom'
import { ArrowRight, ArrowUpRight, BookOpen, Check, ChevronRight, FileText, Layers, MousePointer2, Quote, Route, ShieldCheck } from 'lucide-react'
import PublicLayout from '../components/PublicLayout'
import { demoPaper } from '../data/demoPaper'

export default function Welcome() {
  return (
    <PublicLayout title="把论文读成自己的知识">
      <main id="main-content">
        <section className="lf-hero lf-container">
          <div className="lf-hero-copy">
            <p className="lf-eyebrow"><span /> FOR THE CURIOUS MIND</p>
            <h1>把一篇论文，<br />读成自己的<span className="lf-emphasis">知识。</span></h1>
            <p className="lf-hero-description">面向医学与护理科研的文献精读工具。<br className="lf-desktop-break" />从一句原文出发，读懂方法、追问证据，<br className="lf-desktop-break" />留下下次还能用的卡片与汇报材料。</p>
            <div className="lf-hero-actions">
              <Link to="/demo" className="lf-button">免费试读一篇 <ArrowRight size={17} /></Link>
              <Link to="/" className="lf-text-link">打开自己的论文 <ArrowUpRight size={16} /></Link>
            </div>
            <p className="lf-quiet"><Check size={14} />免费样例 <span>·</span> 预设解读</p>
          </div>
          <div className="lf-hero-art" aria-label="原文、精读与阅读卡片示意">
            <div className="lf-art-orbit" />
            <div className="lf-paper-preview">
              <div className="lf-paper-top"><span>OPEN ACCESS · PLOS ONE</span><BookOpen size={16} /></div>
              <h2>从原文里，<br />找到判断的依据。</h2>
              <p className="lf-paper-en">{demoPaper.title}</p>
              <div className="lf-preview-rule" />
              <span className="lf-tiny-label">RESULTS · 原文节选</span>
              <p className="lf-preview-quote">PPCs were more frequent in current smokers compared to never smokers <mark>(27% vs 17%, p = 0.036)</mark></p>
              <Link to="/demo?section=result" className="lf-selection-button"><MousePointer2 size={14} />精读这段 <ChevronRight size={14} /></Link>
              <div className="lf-paper-foot"><span>Jeganathan et al. · 2022</span><span>CC BY 4.0</span></div>
            </div>
            <div className="lf-floating-card">
              <span className="lf-card-label"><Layers size={14} />发现卡 <span>解读示例</span></span>
              <p>相差 10 个百分点，<br />比较对象也要一起记下。</p>
              <Link to="/demo?section=result" className="lf-anchor-link"><Quote size={12} />回到 Results 原文 <ArrowUpRight size={13} /></Link>
            </div>
            <span className="lf-art-caption">READ. QUESTION. MAKE IT YOURS.</span>
          </div>
        </section>

        <div className="lf-proof-strip lf-container" aria-label="核心能力">
          <span><FileText size={18} /><b>PDF / Zotero</b> 从已有文献开始</span>
          <span><Quote size={18} /><b>原文锚点</b> 核对解读的出处</span>
          <span><Layers size={18} /><b>卡片 / 汇报</b> 把读过的留下来</span>
        </div>

        <section className="lf-section lf-container" id="how-it-works">
          <div className="lf-section-heading"><div><p className="lf-eyebrow">A READING THAT STAYS</p><h2>从读进去，到讲出来。</h2></div><p>一次精读，沿着三个清楚的动作展开。</p></div>
          <div className="lf-steps">
            {[
              { icon: Route, label: '01 / 找到起点', title: '先看懂这篇在问什么', text: 'PDF 或 Zotero 文献进入精读台，沿阅读路线梳理研究问题、方法和主要结果。', example: '研究问题 → 设计 → 结果' },
              { icon: MousePointer2, label: '02 / 读到证据', title: '在卡住的地方，读深一点', text: '选中原文，拆解术语、数字和推论。带着具体问题追问，再回到原文核对。', example: '选段 → 拆解 → 原文核对' },
              { icon: Layers, label: '03 / 留下成果', title: '让理解变成可复用的内容', text: '方法、发现、批判和迁移形成阅读卡片；整理进汇报板，导出 PPT 或 Markdown。', example: '卡片 → 汇报板 → 导出' },
            ].map(item => <article className="lf-step" key={item.label}>
              <div className="lf-step-head"><span>{item.label}</span><item.icon size={21} strokeWidth={1.5} /></div>
              <h3>{item.title}</h3><p>{item.text}</p><div className="lf-step-example">{item.example}</div>
            </article>)}
          </div>
        </section>

        <section className="lf-sample-section lf-container">
          <div className="lf-sample-intro"><p className="lf-eyebrow">ONE PAPER. THREE QUESTIONS.</p><h2>先从这一篇，<br />试着读深一点。</h2><p>用一篇开放获取论文，体验研究设计、结果解释和局限追问。示例已备好，点开即可开始。</p><Link to="/demo" className="lf-button lf-button-light">进入免费试读 <ArrowRight size={17} /></Link></div>
          <div className="lf-sample-paper"><div className="lf-sample-meta"><span>医学研究 · 回顾性分析</span><span>2022</span></div><h3>{demoPaper.titleZh}</h3><p>{demoPaper.title}</p><div className="lf-sample-questions">{demoPaper.sections.map(section => <Link to={`/demo?section=${section.id}`} key={section.id}><span>{section.number}</span>{section.question}<ArrowUpRight size={15} /></Link>)}</div><a href={demoPaper.url} target="_blank" rel="noreferrer">Jeganathan et al. · PLOS ONE · CC BY 4.0 <ArrowUpRight size={13} /></a></div>
        </section>

        <section className="lf-section lf-container lf-faq" id="questions">
          <div><p className="lf-eyebrow">BEFORE THE FIRST PAGE</p><h2>开始前的几个问题。</h2></div>
          <div className="lf-faq-list">
            <details><summary>需要先注册或配置 AI 吗？</summary><p>免费样例点开即可试读。使用 Zotero 账号登录后，可跨浏览器访问同一阅读空间；AI 服务在「设置」中管理。</p></details>
            <details><summary>论文和阅读记录如何处理？</summary><p>试读卡片保存在当前浏览器。正式阅读的 PDF、笔记和对话保存在研雀服务器；AI 阅读会将相关内容发送至所选模型服务。</p></details>
            <details><summary>网站和 Zotero 插件是什么关系？</summary><p>通过 Zotero 插件把文献送入研雀，在网页里精读、提问和整理卡片。</p></details>
            <details><summary>手机上可以做什么？</summary><p>手机可以完整走完这个试读样例，也可访问网页书架与阅读记录。多栏 PDF 精读、卡片整理和汇报制作，建议使用电脑。</p></details>
          </div>
        </section>
        <section className="lf-bottom-cta lf-container"><ShieldCheck size={27} strokeWidth={1.4} /><div><h2>从一篇论文开始，留下一点真正读懂的东西。</h2><p>免费样例已准备好。</p></div><Link to="/demo" className="lf-button">开始试读 <ArrowRight size={16} /></Link></section>
      </main>
    </PublicLayout>
  )
}
