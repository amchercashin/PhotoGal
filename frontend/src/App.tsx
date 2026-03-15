import { useEffect, useRef } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Topbar } from './components/layout/Topbar'
import { Sidebar } from './components/layout/Sidebar'
import { PipelineBanner } from './components/layout/PipelineBanner'
import { GpuUpgradeBanner } from './components/layout/GpuUpgradeBanner'
import { GalleryGrid } from './components/gallery/GalleryGrid'
import { PhotoViewer } from './components/viewer/PhotoViewer'
import { AllPhotosTable } from './components/table/AllPhotosTable'
import { PeopleGrid } from './components/people/PeopleGrid'
import { useUIStore } from './store/ui'
import { api } from './api/client'
import { usePipelineSync } from './hooks/usePipelineSync'
import { ToastContainer } from './components/shared/Toast'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
})

function AppContent() {
  const { tab, sidebarOpen, sidebarWidth, setSidebarWidth } = useUIStore()
  usePipelineSync()
  const isDragging = useRef(false)

  useEffect(() => {
    api.triggerSyncCheck().catch(() => {/* best-effort */})
  }, [])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      setSidebarWidth(e.clientX)
    }
    const onUp = () => { isDragging.current = false }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [setSidebarWidth])

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-neutral-950">
      <Topbar />
      <PipelineBanner />
      <GpuUpgradeBanner />
      <div className="flex flex-1 overflow-hidden">
        {sidebarOpen && (
          <>
            <div style={{ width: sidebarWidth }} className="shrink-0 overflow-hidden">
              <Sidebar />
            </div>
            {/* Drag handle */}
            <div
              className="w-1 shrink-0 cursor-col-resize hover:bg-blue-500/50 bg-neutral-800 select-none"
              onMouseDown={() => { isDragging.current = true }}
            />
          </>
        )}
        <main className="flex-1 overflow-hidden flex flex-col">
          <div className="flex-1 overflow-hidden">
            {tab === 'gallery' && <GalleryGrid />}
            {tab === 'viewer' && <PhotoViewer />}
            {tab === 'table' && <AllPhotosTable />}
            {tab === 'people' && <PeopleGrid />}
          </div>
        </main>
      </div>
      <ToastContainer />
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  )
}
