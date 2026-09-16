import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { IdentityProvider } from './lib/identity'
import { ProjectProvider } from './lib/project'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <IdentityProvider>
        <ProjectProvider>
          <App />
        </ProjectProvider>
      </IdentityProvider>
    </BrowserRouter>
  </StrictMode>,
)
