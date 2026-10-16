import { Link } from 'react-router-dom'
import { ArrowRight, BookOpen } from 'lucide-react'
import PublicLayout from '../components/PublicLayout'
import AccountGate, { AccountCard } from '../components/AccountGate'

export default function Login() {
  return <PublicLayout title="连接阅读空间">
    <main id="main-content" className="lf-login-page lf-container">
      <div className="lf-login-intro"><BookOpen size={30} strokeWidth={1.4} /><p className="lf-eyebrow">CONTINUE THE READING</p><h1>连接阅读空间。</h1><p>使用 Zotero 账号登录研雀。</p></div>
      <div className="lf-login-card"><AccountGate><AccountCard /><Link to="/" className="lf-button">进入精读工作台 <ArrowRight size={16} /></Link></AccountGate><div className="lf-login-sample"><span>先看看研雀怎样精读？</span><Link to="/demo">免费试读，无需登录 <ArrowRight size={14} /></Link></div></div>
    </main>
  </PublicLayout>
}
