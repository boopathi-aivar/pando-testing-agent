import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Sidebar from './components/layout/Sidebar'
import TopBar from './components/layout/TopBar'
import Dashboard from './pages/Dashboard'
import Configure from './pages/Configure'
import Results from './pages/Results'
import Login from './pages/Login'
import SettingsPage from './pages/Settings'
import Projects from './pages/Projects'
import Documentation from './pages/Documentation'
import DocumentationDetail from './pages/DocumentationDetail'
import { getToken } from './api/client'

const SIDEBAR_KEY = 'pando_sidebar_open'
const SIDEBAR_WIDTH = 300
const SIDEBAR_COLLAPSED = 64

function PrivateRoute({ children }) {
  return getToken() ? children : <Navigate to="/login" replace />
}

function Layout({ title, children }) {
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const stored = localStorage.getItem(SIDEBAR_KEY)
      return stored === null ? true : stored === 'true'
    } catch {
      return true
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, String(sidebarOpen))
    } catch { /* ignore */ }
  }, [sidebarOpen])

  const offset = sidebarOpen ? SIDEBAR_WIDTH : SIDEBAR_COLLAPSED

  return (
    <div className="min-h-screen bg-background">
      <Sidebar open={sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
      <TopBar
        title={title}
        sidebarOpen={sidebarOpen}
      />
      <main
        className="pt-16 min-h-screen bg-background transition-[margin] duration-200 ease-out"
        style={{ marginLeft: offset }}
      >
        <div className="w-full max-w-[1680px] mx-auto px-8 py-8">
          {children}
        </div>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<PrivateRoute><Layout title="Dashboard"><Dashboard /></Layout></PrivateRoute>} />
        <Route path="/projects" element={<PrivateRoute><Layout title="Projects"><Projects /></Layout></PrivateRoute>} />
        <Route path="/project/:projectId/configure" element={<PrivateRoute><Layout title="Configure Project"><Configure /></Layout></PrivateRoute>} />
        <Route path="/project/:projectId/results" element={<PrivateRoute><Layout title="Test Results"><Results /></Layout></PrivateRoute>} />
        <Route path="/documentation" element={<PrivateRoute><Layout title="Documentation"><Documentation /></Layout></PrivateRoute>} />
        <Route path="/documentation/:docId" element={<PrivateRoute><Layout title="Documentation"><DocumentationDetail /></Layout></PrivateRoute>} />
        <Route path="/settings" element={<PrivateRoute><Layout title="Settings"><SettingsPage /></Layout></PrivateRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
