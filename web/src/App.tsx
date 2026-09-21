import { Navigate, Route, Routes } from 'react-router-dom'

import { Shell } from './components/Shell'
import { Dashboard } from './pages/Dashboard'
import { Projects } from './pages/Projects'
import { Setup } from './pages/Setup'
import { Studio } from './pages/Studio'
import { Monitor } from './pages/Monitor'
import { Analyze } from './pages/Analyze'
import { Models } from './pages/Models'
import { Gpu } from './pages/Gpu'
import { Operations } from './pages/Operations'
import { Users } from './pages/Users'
import { Copilot } from './pages/Copilot'

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/setup" element={<Setup />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/monitor" element={<Monitor />} />
        <Route path="/analyze" element={<Analyze />} />
        <Route path="/models" element={<Models />} />
        <Route path="/operations" element={<Operations />} />
          <Route path="/gpu" element={<Gpu />} />
        <Route path="/users" element={<Users />} />
        <Route path="/copilot" element={<Copilot />} />
      </Routes>
    </Shell>
  )
}
