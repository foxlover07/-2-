import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { readStorage, writeStorage } from '../utils/storage'

// 찜 목록 (2026-11-16). 비교 목록(최대 3개)과 달리 개수 제한 없이 마음에 드는 매물을 모아 두는 용도.
// 이 브라우저에만 저장한다 (서버로 보내지 않음).
const STORAGE_KEY = 'home-poc:favorites'

interface FavoriteContextValue {
  ids: string[]
  has: (id: string) => boolean
  toggle: (id: string) => void
}

const FavoriteContext = createContext<FavoriteContextValue | null>(null)

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'string')
}

export function FavoriteProvider({ children }: { children: ReactNode }) {
  const [ids, setIds] = useState<string[]>(() => [...new Set(readStorage(STORAGE_KEY, isStringArray) ?? [])])

  useEffect(() => {
    writeStorage(STORAGE_KEY, ids)
  }, [ids])

  const value: FavoriteContextValue = {
    ids,
    has: (id) => ids.includes(id),
    toggle: (id) => setIds((prev) => (prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id])),
  }

  return <FavoriteContext value={value}>{children}</FavoriteContext>
}

export function useFavorites(): FavoriteContextValue {
  const value = useContext(FavoriteContext)
  if (!value) throw new Error('useFavorites는 FavoriteProvider 안에서만 사용할 수 있습니다.')
  return value
}
