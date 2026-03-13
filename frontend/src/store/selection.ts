/**
 * Global selection & marking state.
 *
 * Selection = currently active/focused item (for navigation, one at a time or range)
 * Marking   = items flagged for bulk action (Space bar toggle, persists across tabs)
 *
 * Items are identified by { type: 'photo'|'cluster', id: number }
 */

import { create } from 'zustand'

export type ItemRef = { type: 'photo' | 'cluster'; id: number }

function itemKey(ref: ItemRef) {
  return `${ref.type}:${ref.id}`
}

export function parseItemKey(key: string): ItemRef | null {
  if (key.startsWith('photo:')) return { type: 'photo', id: parseInt(key.slice(6)) }
  if (key.startsWith('cluster:')) return { type: 'cluster', id: parseInt(key.slice(8)) }
  return null
}

interface SelectionState {
  selected: Set<string>
  marked: Set<string>

  selectOne: (ref: ItemRef) => void
  selectMany: (refs: ItemRef[]) => void
  selectRange: (refs: ItemRef[], fromIdx: number, toIdx: number) => void
  toggleSelect: (ref: ItemRef) => void
  clearSelection: () => void
  toggleMarkSelected: () => void
  toggleMarkCluster: (photoIds: number[]) => void
  markItems: (refs: ItemRef[], marked: boolean) => void
  clearMarks: () => void
  getSelectedKeys: () => string[]
  getMarkedIds: (type: 'photo' | 'cluster') => number[]
  isSelected: (ref: ItemRef) => boolean
  isMarked: (ref: ItemRef) => boolean
  isClusterMarked: (photoIds: number[]) => boolean
}

export const useSelectionStore = create<SelectionState>((set, get) => ({
  selected: new Set(),
  marked: new Set(),

  selectOne: (ref) => set({ selected: new Set([itemKey(ref)]) }),

  selectMany: (refs) => set({ selected: new Set(refs.map(itemKey)) }),

  selectRange: (refs, fromIdx, toIdx) => {
    const start = Math.min(fromIdx, toIdx)
    const end = Math.max(fromIdx, toIdx)
    const keys = refs.slice(start, end + 1).map(itemKey)
    set({ selected: new Set(keys) })
  },

  toggleSelect: (ref) => {
    const key = itemKey(ref)
    const next = new Set(get().selected)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    set({ selected: next })
  },

  clearSelection: () => set({ selected: new Set() }),

  toggleMarkSelected: () => {
    const { selected, marked } = get()
    const next = new Set(marked)
    const allMarked = [...selected].every((k) => next.has(k))
    if (allMarked) {
      selected.forEach((k) => next.delete(k))
    } else {
      selected.forEach((k) => next.add(k))
    }
    set({ marked: next })
  },

  markItems: (refs, shouldMark) => {
    const next = new Set(get().marked)
    refs.forEach((ref) => {
      const key = itemKey(ref)
      if (shouldMark) next.add(key)
      else next.delete(key)
    })
    set({ marked: next })
  },

  toggleMarkCluster: (photoIds) => {
    const { marked } = get()
    const allMarked = photoIds.length > 0 && photoIds.every((id) => marked.has(`photo:${id}`))
    const next = new Set(marked)
    if (allMarked) {
      photoIds.forEach((id) => next.delete(`photo:${id}`))
    } else {
      photoIds.forEach((id) => next.add(`photo:${id}`))
    }
    set({ marked: next })
  },

  clearMarks: () => set({ marked: new Set() }),

  getSelectedKeys: () => [...get().selected],

  getMarkedIds: (type) => {
    const prefix = `${type}:`
    return [...get().marked]
      .filter((k) => k.startsWith(prefix))
      .map((k) => parseInt(k.slice(prefix.length)))
  },

  isSelected: (ref) => get().selected.has(itemKey(ref)),
  isMarked: (ref) => get().marked.has(itemKey(ref)),
  isClusterMarked: (photoIds) => photoIds.some((id) => get().marked.has(`photo:${id}`)),
}))
