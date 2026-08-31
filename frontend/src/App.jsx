import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/layout/Sidebar'
import TopBar from './components/layout/TopBar'
import Dashboard from './pages/Dashboard'
import Configure from './pages/Configure'
import Results from './pages/Results'
import Login from './pages/Login'
import SettingsPage from './pages/Settings'
import Projects from './pages/Projects'
import { getToken } from './api/client'

// ─── Auth guard ───────────────────────────────────────────────────────────────
function PrivateRoute({ children }) {
  return getToken() ? children : <Navigate to="/login" replace />
}

// ─── Shared app layout ────────────────────────────────────────────────────────
function Layout({ title, children }) {
  return (
    <div>
      <Sidebar />
      <TopBar title={title} />
      <main className="ml-[220px] pt-14 min-h-screen bg-background">
        {children}
      </main>
    </div>
  )
}


// ─── App ──────────────────────────────────────────────────────────────────────
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<Login />} />

        {/* Protected */}
        <Route path="/" element={<PrivateRoute><Layout title="Dashboard"><Dashboard /></Layout></PrivateRoute>} />
        <Route path="/projects" element={<PrivateRoute><Layout title="Projects"><Projects /></Layout></PrivateRoute>} />
        <Route path="/project/:projectId/configure" element={<PrivateRoute><Layout title="Configure Project"><Configure /></Layout></PrivateRoute>} />
        <Route path="/project/:projectId/results" element={<PrivateRoute><Layout title="Test Results"><Results /></Layout></PrivateRoute>} />
        <Route path="/settings" element={<PrivateRoute><Layout title="Settings"><SettingsPage /></Layout></PrivateRoute>} />

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
