import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { initApi } from './api/client'
import App from './App.tsx'

initApi().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
