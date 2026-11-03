import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { readStorage, writeStorage } from '../utils/storage'
import type { SpotVisits } from '../utils/walk'

const STORAGE_KEY = 'home-poc:profile'

export const IMPORTANCE_MIN = 1
export const IMPORTANCE_MAX = 5

export interface Profile {
  // 선택한 강의동만 키로 들어간다.
  visits: SpotVisits
  budgetManwon: number | null
  costImportance: number
  walkImportance: number
}

export const EMPTY_PROFILE: Profile = {
  visits: {},
  budgetManwon: null,
  costImportance: 3,
  walkImportance: 3,
}

function isImportance(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= IMPORTANCE_MIN && v <= IMPORTANCE_MAX
}

function isProfile(value: unknown): value is Profile {
  if (typeof value !== 'object' || value === null) return false
  const p = value as Profile
  return (
    typeof p.visits === 'object' &&
    p.visits !== null &&
    !Array.isArray(p.visits) &&
    Object.values(p.visits).every((n) => typeof n === 'number' && Number.isInteger(n) && n >= 0) &&
    (p.budgetManwon === null || (typeof p.budgetManwon === 'number' && p.budgetManwon >= 0)) &&
    isImportance(p.costImportance) &&
    isImportance(p.walkImportance)
  )
}

interface ProfileContextValue {
  profile: Profile
  // 저장된 적이 있는지. 없으면 화면에서 프로필 설정을 안내한다.
  isSaved: boolean
  saveProfile: (next: Profile) => void
}

const ProfileContext = createContext<ProfileContextValue | null>(null)

export function ProfileProvider({ children }: { children: ReactNode }) {
  const [saved, setSaved] = useState<Profile | null>(() => readStorage(STORAGE_KEY, isProfile))

  useEffect(() => {
    if (saved) writeStorage(STORAGE_KEY, saved)
  }, [saved])

  return (
    <ProfileContext value={{ profile: saved ?? EMPTY_PROFILE, isSaved: saved !== null, saveProfile: setSaved }}>
      {children}
    </ProfileContext>
  )
}

export function useProfile(): ProfileContextValue {
  const value = useContext(ProfileContext)
  if (!value) throw new Error('useProfile은 ProfileProvider 안에서만 사용할 수 있습니다.')
  return value
}
