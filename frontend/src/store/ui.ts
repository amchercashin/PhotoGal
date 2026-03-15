/**
 * UI state: current tab, zoom level, sidebar visibility,
 * unified cross-tab selection, per-tab preserved state.
 */

import { create } from 'zustand'

export type Tab = 'gallery' | 'viewer' | 'table' | 'people'

interface UIState {
  tab: Tab
  zoom: 1 | 2 | 3  // 1=small, 2=medium, 3=large
  viewerPhotoId: number | null
  sidebarOpen: boolean
  sidebarWidth: number

  // Cross-tab unified selection — the single source of truth for "what's selected"
  activePhotoId: number | null
  activeClusterId: number | null
  activePerson: number | null

  // Gallery tab preserved state
  galleryScrollOffset: number

  // Pipeline
  isPipelineRunning: boolean
  setIsPipelineRunning: (v: boolean) => void

  // Search
  searchQuery: string
  setSearchQuery: (q: string) => void

  // Table tab preserved state
  tablePage: number
  tableSortBy: string
  tableSortDir: 'ASC' | 'DESC'
  tableFilterCategory: string | undefined
  tableScrollOffset: number

  setTab: (tab: Tab) => void
  setZoom: (zoom: 1 | 2 | 3) => void
  zoomIn: () => void
  zoomOut: () => void
  openViewer: (photoId: number) => void
  closeViewer: () => void
  toggleSidebar: () => void
  setSidebarWidth: (w: number) => void

  // Unified selection setters
  setActivePhoto: (photoId: number | null, clusterId?: number | null) => void
  setActiveCluster: (clusterId: number | null, photoId?: number | null) => void
  setActivePerson: (id: number | null) => void

  // Per-tab state setters
  setGalleryScrollOffset: (offset: number) => void
  setTablePage: (page: number) => void
  setTableSortBy: (sortBy: string) => void
  setTableSortDir: (dir: 'ASC' | 'DESC') => void
  setTableFilterCategory: (cat: string | undefined) => void
  setTableScrollOffset: (offset: number) => void
}

export const useUIStore = create<UIState>((set, get) => ({
  tab: 'gallery',
  zoom: 2,
  viewerPhotoId: null,
  sidebarOpen: true,
  sidebarWidth: 224,

  activePhotoId: null,
  activeClusterId: null,
  activePerson: null,

  galleryScrollOffset: 0,

  isPipelineRunning: false,
  setIsPipelineRunning: (v) => {
    if (get().isPipelineRunning !== v) set({ isPipelineRunning: v })
  },

  searchQuery: '',
  setSearchQuery: (q) => set({ searchQuery: q }),

  tablePage: 0,
  tableSortBy: 'exif_date',
  tableSortDir: 'ASC',
  tableFilterCategory: undefined,
  tableScrollOffset: 0,

  setTab: (tab) => set({ tab }),
  setZoom: (zoom) => set({ zoom }),
  zoomIn: () => {
    const z = get().zoom
    if (z < 3) set({ zoom: (z + 1) as 1 | 2 | 3 })
  },
  zoomOut: () => {
    const z = get().zoom
    if (z > 1) set({ zoom: (z - 1) as 1 | 2 | 3 })
  },
  openViewer: (photoId) => set({ tab: 'viewer', viewerPhotoId: photoId }),
  closeViewer: () => set({ tab: 'gallery', viewerPhotoId: null }),
  toggleSidebar: () => set({ sidebarOpen: !get().sidebarOpen }),
  setSidebarWidth: (w) => set({ sidebarWidth: Math.max(120, Math.min(400, w)) }),

  setActivePhoto: (photoId, clusterId) => set({
    activePhotoId: photoId,
    ...(clusterId !== undefined ? { activeClusterId: clusterId } : {}),
  }),
  setActiveCluster: (clusterId, photoId) => set({
    activeClusterId: clusterId,
    ...(photoId !== undefined ? { activePhotoId: photoId } : {}),
  }),
  setActivePerson: (id) => set({ activePerson: id }),

  setGalleryScrollOffset: (offset) => set({ galleryScrollOffset: offset }),
  setTablePage: (page) => set({ tablePage: page }),
  setTableSortBy: (sortBy) => set({ tableSortBy: sortBy }),
  setTableSortDir: (dir) => set({ tableSortDir: dir }),
  setTableFilterCategory: (cat) => set({ tableFilterCategory: cat }),
  setTableScrollOffset: (offset) => set({ tableScrollOffset: offset }),
}))
