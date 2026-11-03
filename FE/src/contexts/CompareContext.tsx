import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { readStorage, writeStorage } from '../utils/storage'

export const MAX_COMPARE = 3
const STORAGE_KEY = 'home-poc:compare'

interface CompareContextValue {
  ids: string[]
  isFull: boolean
  has: (id: string) => boolean
  add: (id: string) => void
  remove: (id: string) => void
  toggle: (id: string) => void
  clear: () => void
}

const CompareContext = createContext<CompareContextValue | null>(null)

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'string')
}

export function CompareProvider({ children }: { children: ReactNode }) {
  const [ids, setIds] = useState<string[]>(
    () => [...new Set(readStorage(STORAGE_KEY, isStringArray) ?? [])].slice(0, MAX_COMPARE),
  )

  useEffect(() => {
    writeStorage(STORAGE_KEY, ids)
  }, [ids])

  const add = (id: string) =>
    setIds((prev) => (prev.includes(id) || prev.length >= MAX_COMPARE ? prev : [...prev, id]))
  const remove = (id: string) => setIds((prev) => prev.filter((v) => v !== id))

  const value: CompareContextValue = {
    ids,
    isFull: ids.length >= MAX_COMPARE,
    has: (id) => ids.includes(id),
    add,
    remove,
    toggle: (id) => (ids.includes(id) ? remove(id) : add(id)),
    clear: () => setIds([]),
  }

  return <CompareContext value={value}>{children}</CompareContext>
}

export function useCompare(): CompareContextValue {
  const value = useContext(CompareContext)
  if (!value) throw new Error('useCompare는 CompareProvider 안에서만 사용할 수 있습니다.')
  return value
}
