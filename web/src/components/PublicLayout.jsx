import { useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ArrowUpRight, BookOpen } from 'lucide-react'
import '../styles/public.css'

export default function PublicLayout({ children, title, demo = false }) {
  const { pathname, hash } = useLocation()
  useEffect(() => {
    document.title = `${title} · 研雀 LitFinch`
    if (hash === '#questions') document.getElementById('questions')?.scrollIntoView()
    else window.scrollTo(0, 0)
    return () => { document.title = '研雀 · LitFinch' }
  }, [title, pathname, hash])

  return (
    <div className={`lf-public ${demo ? 'lf-public-demo' : ''}`}>
      <a href="#main-content" className="lf-skip">跳到正文</a>
      <header className="lf-header">
        <Link to="/welcome" className="lf-brand" aria-label="研雀 · LitFinch 产品首页">
          <img src="/icon-light.svg" alt="" width="38" height="38" />
          <span>研雀 <span className="lf-brand-en">LitFinch</span></span>
        </Link>
        <nav aria-label="产品导航">
          <Link to="/demo" className="lf-nav-demo" aria-current={demo ? 'page' : undefined}>试读一篇</Link>
          <Link to="/login" className="lf-login-link">登录</Link>
          <Link to="/" className="lf-button lf-button-small lf-header-workspace">进入工作台 <ArrowUpRight size={15} /></Link>
        </nav>
      </header>
      {children}
      <footer className="lf-footer">
        <span><BookOpen size={15} />研雀 · LitFinch<span className="lf-footer-note">文献精读与知识沉淀</span></span>
        <div><Link to="/welcome#questions">使用说明</Link><Link to="/demo">免费试读</Link><Link to="/">精读工作台 <ArrowUpRight size={13} /></Link></div>
      </footer>
    </div>
  )
}
