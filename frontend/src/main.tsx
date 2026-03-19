import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { initApi } from './api/client'
import App from './App.tsx'

initApi().then((result) => {
  if (!result.ok) {
    // Show error on loading screen (React not mounted yet)
    const spinner = document.getElementById('loading-spinner')
    const title = document.getElementById('loading-title')
    const status = document.getElementById('loading-status')
    const errorEl = document.getElementById('loading-error')
    const logPathEl = document.getElementById('loading-log-path')
    const retryBtn = document.getElementById('loading-retry-btn')

    if (spinner) spinner.style.display = 'none'
    if (title) title.style.color = '#e55'
    if (status) status.style.display = 'none'
    if (errorEl) {
      errorEl.style.display = 'block'
      errorEl.textContent = result.error || 'Не удалось подключиться к серверу'
    }
    if (logPathEl && result.logPath) {
      logPathEl.style.display = 'block'
      logPathEl.textContent = `Лог: ${result.logPath}`
    }
    if (retryBtn) retryBtn.style.display = 'inline-block'
    return
  }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
