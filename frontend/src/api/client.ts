let BASE = '/api'

/** Detect Tauri and set the correct backend base URL. Call before rendering. */
export async function initApi() {
  if ('__TAURI_INTERNALS__' in window) {
    const { invoke } = await import('@tauri-apps/api/core')
    const port = await invoke<number>('get_backend_port')
    BASE = `http://127.0.0.1:${port}/api`
  }
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${method} ${path} → ${res.status}: ${text}`)
  }
  return res.json()
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body: unknown) => request<T>('POST', path, body)
const patch = <T>(path: string, body: unknown) => request<T>('PATCH', path, body)
const del = <T>(path: string) => request<T>('DELETE', path)

// --- Types ---

export interface Photo {
  id: number
  content_hash: string | null
  source_id: number | null
  original_path: string
  original_filename: string
  current_path: string | null
  file_size: number | null
  processing_level: number
  cluster_id: number | null
  exif_date: string | null
  exif_gps_lat: number | null
  exif_gps_lon: number | null
  exif_camera: string | null
  exif_orientation: number | null
  exif_width: number | null
  exif_height: number | null
  location_country: string | null
  location_city: string | null
  location_district: string | null
  quality_blur: number | null
  quality_exposure: number | null
  quality_aesthetic: number | null
  face_count: number | null
  is_technical: number
  content_category: string | null
  rank_in_cluster: number | null
  user_decision: 'keep' | 'delete' | 'archive' | null
  sync_status: 'ok' | 'disconnected'
  is_exact_duplicate: number
  created_at: string
  updated_at: string
}

export interface Cluster {
  id: number
  name: string | null
  best_photo_id: number | null
  photo_count: number
  type: 'content' | 'singleton' | 'dup'
  avg_timestamp: string | null
  avg_gps_lat: number | null
  avg_gps_lon: number | null
  location_city: string | null
  photo_ids?: number[]
  photos?: Photo[]
  best_photo_blur: number | null
  best_photo_exposure: number | null
  has_exact_duplicate: boolean
}

export interface ClusterListResult {
  items: Cluster[]
  total: number
  offset: number
  limit: number
}

export interface Source {
  id: number
  path: string
  name: string | null
  added_at: string
  last_scanned_at: string | null
  photo_count: number
  status: string
}

export interface PhotoListResult {
  total: number
  offset: number
  limit: number
  items: Photo[]
}

export interface PipelineStatus {
  running: boolean
  level: number | null
  source_id: number | null
  progress: number
  total: number
  stage: string | null
  started_at: string | null
  error: string | null
  elapsed_s: number
  stage_elapsed_s: number
}

export interface Stats {
  total_photos: number
  by_level: Record<number, number>
  clusters: number
  technical_photos: number
  sources: number
  disconnected: number
  category_counts: Record<string, number>
}

export interface SyncStatus {
  running: boolean
  checked: number
  total: number
  disconnected: number
}

export interface SearchResult {
  photo_id: number
  similarity: number
}

export interface SearchResponse {
  query: string
  translated_query: string | null
  results: SearchResult[]
  total_with_embeddings: number
  elapsed_ms: number
}

export interface Person {
  id: number
  name: string | null
  face_count: number
  representative_face_id: number | null
  hidden: boolean
}

export interface FaceOnPhoto {
  id: number
  bbox_x: number
  bbox_y: number
  bbox_w: number
  bbox_h: number
  person_id: number | null
  person_name: string | null
  confidence: number
}

// --- API functions ---

export const api = {
  // Sources
  getSources: () => get<Source[]>('/sources/'),
  addSource: (path: string, name?: string) => post<Source>('/sources/', { path, name }),
  removeSource: (id: number, deletePhotos = false) => del<{ ok: boolean }>(`/sources/${id}?delete_photos=${deletePhotos}`),

  // Photos
  getPhotos: (params: {
    limit?: number; offset?: number; sort_by?: string; sort_dir?: string
    filter_level?: number; filter_category?: string; filter_cluster_id?: number
  } = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) q.set(k, String(v))
    })
    return get<PhotoListResult>(`/photos/?${q}`)
  },
  getPhoto: (id: number) => get<Photo>(`/photos/${id}`),
  updatePhoto: (id: number, data: { user_decision: string | null }) =>
    patch<{ ok: boolean }>(`/photos/${id}`, data),
  getStats: () => get<Stats>('/photos/stats'),
  thumbnailUrl: (id: number, version?: string | null) => `${BASE}/photos/${id}/thumbnail${version ? `?v=${version.slice(0, 8)}` : ''}`,
  fullUrl: (id: number) => `${BASE}/photos/${id}/full`,

  // Clusters
  getClusters: (params: { nonempty?: boolean; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams()
    q.set('nonempty', String(params.nonempty ?? true))
    if (params.limit !== undefined) q.set('limit', String(params.limit))
    if (params.offset !== undefined) q.set('offset', String(params.offset))
    return get<ClusterListResult>(`/clusters/?${q}`)
  },
  getCluster: (id: number) => get<Cluster>(`/clusters/${id}`),
  getClusterPhotoIds: (cluster_ids: number[]) =>
    post<Record<number, number[]>>('/clusters/photo-ids', { cluster_ids }),

  // Pipeline
  getPipelineStatus: () => get<PipelineStatus>('/process/status'),
  runLevel: (level: number, source_id?: number) =>
    post<{ ok: boolean }>('/process/run', { level, source_id }),
  stopPipeline: () => post<{ ok: boolean }>('/process/stop', {}),

  // Sync
  triggerSyncCheck: () => post<{ ok: boolean }>('/sync/check', {}),
  getSyncStatus: () => get<SyncStatus>('/sync/status'),

  // Photo IDs by level/sync
  getPhotoIdsByLevel: (level: number) => get<{ ids: number[]; cluster_ids: number[] }>(`/photos/ids-by-level/${level}`),
  getPhotoIdsBySync: (status: string) => get<{ ids: number[]; cluster_ids: number[] }>(`/photos/ids-by-sync/${status}`),
  getPhotoLevelInfo: (photo_ids: number[]) =>
    post<{ min_level: number; disconnected_count: number; active_count: number; level_counts: Record<number, number> }>('/photos/level-info', { photo_ids }),
  getPhotoTablePosition: (id: number, params: { sort_by: string; sort_dir: string; filter_category?: string; page_size?: number }) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) q.set(k, String(v))
    })
    return get<{ row_index: number; page: number; found: boolean }>(`/photos/${id}/table-position?${q}`)
  },

  // Bulk operations
  deletePhotosBulk: (photo_ids: number[]) => post<{ deleted: number; trashed: number; errors: string[] }>('/photos/bulk-delete', { photo_ids }),
  runMarked: (photo_ids: number[], targetLevel = 3) =>
    post<{ ok: boolean }>('/process/run-marked', { photo_ids, target_level: targetLevel }),
  estimateProcessing: (photo_count: number) =>
    post<{ estimated_seconds: number; rate_per_photo_ms: number; source: string }>(
      '/process/estimate', { photo_count }
    ),

  // Search
  searchPhotos: (query: string, limit = 200) =>
    post<SearchResponse>('/search/', { query, limit }),

  // Persons & Faces
  listPersons: (params?: { include_hidden?: boolean; limit?: number; offset?: number }) => {
    const p = new URLSearchParams()
    if (params?.include_hidden) p.set('include_hidden', 'true')
    if (params?.limit) p.set('limit', String(params.limit))
    if (params?.offset) p.set('offset', String(params.offset))
    const qs = p.toString()
    return get<Person[]>(`/persons/${qs ? '?' + qs : ''}`)
  },

  getPersonPhotos: (personId: number) =>
    get<{ photo_ids: number[]; total: number }>(`/persons/${personId}/photos`),

  updatePerson: (personId: number, data: { name?: string; hidden?: boolean }) =>
    patch<{ ok: boolean }>(`/persons/${personId}`, data),

  getFacesForPhoto: (photoId: number) =>
    get<FaceOnPhoto[]>(`/faces/photo/${photoId}`),

  getFaceThumbUrl: (faceId: number) =>
    `${BASE}/faces/${faceId}/thumb`,

  // Device
  getDeviceInfo: () => get<{
    backend: string
    gpu_detected: string | null
    gpu_backend_installed: boolean
    gpu_validated: boolean | null
    upgrade_available: boolean
    upgrade_size_mb: number | null
    upgrade_benefit: string | null
    current_speed_ms: number
    upgraded_speed_ms: number
    clip_batch_size: number
    face_batch_size: number
  }>('/device/'),

  // Health
  health: () => get<{ status: string }>('/health'),

  // Tauri commands
  revealInFinder: async (path: string) => {
    if ('__TAURI_INTERNALS__' in window) {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('reveal_in_finder', { path })
    }
  },
}
