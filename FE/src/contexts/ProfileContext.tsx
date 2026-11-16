import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { EMPTY_PROFILE, parseStoredProfile, type Profile } from '../utils/profile'
import { readStorage, writeStorage } from '../utils/storage'

export type { ClassSlot, FrequentPlace, Profile } from '../utils/profile'

const STORAGE_KEY = 'home-poc:profile'

const anyValue = (v: unknown): v is unknown => v !== undefined

interface ProfileContextValue {
  profile: Profile
  // 저장된 적이 있는지. 없으면 화면에서 프로필 설정을 안내한다.
  isSaved: boolean
  saveProfile: (next: Profile) => void
}

const ProfileContext = createContext<ProfileContextValue | null>(null)

export function ProfileProvider({ children }: { children: ReactNode }) {
  // 예전 형식 저장값도 읽는다 (새 필드는 기본값).
  const [saved, setSaved] = useState<Profile | null>(() => parseStoredProfile(readStorage(STORAGE_KEY, anyValue)))

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
