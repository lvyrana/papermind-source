import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import Welcome from './pages/Welcome'
import Demo from './pages/Demo'

// Public pages work without an account or backend; workspace imports stay
// lazy so visiting a sample does not initialize the existing device identity.
const WorkspaceRoutes = lazy(() => import('./WorkspaceRoutes'))
const Login = lazy(() => import('./pages/Login'))

export default function App() {
  return (
    <Suspense fallback={<main className="p-8 text-navy" role="status">正在打开研雀…</main>}>
      <Routes>
        <Route path="/welcome" element={<Welcome />} />
        <Route path="/demo" element={<Demo />} />
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<WorkspaceRoutes />} />
      </Routes>
    </Suspense>
  )
}
