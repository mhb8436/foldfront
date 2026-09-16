import { Navigate, Route, Routes } from 'react-router-dom'

import { Shell } from './components/Shell'
import { Dashboard } from './pages/Dashboard'
import { Setup } from './pages/Setup'
import { Studio } from './pages/Studio'
import { Monitor } from './pages/Monitor'
import { Analyze } from './pages/Analyze'
import { Models } from './pages/Models'
import { Operations } from './pages/Operations'

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/setup" element={<Setup />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/monitor" element={<Monitor />} />
        <Route path="/analyze" element={<Analyze />} />
        <Route path="/models" element={<Models />} />
        <Route path="/operations" element={<Operations />} />
      </Routes>
    </Shell>
  )
}
