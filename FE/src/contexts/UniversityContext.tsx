import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { DEFAULT_UNIVERSITY, isUniversityName, type UniversityName } from '../constants/universities'
import { readStorage, writeStorage } from '../utils/storage'

const STORAGE_KEY = 'home-poc:university'

interface UniversityContextValue {
  university: UniversityName
  setUniversity: (name: UniversityName) => void
}

const UniversityContext = createContext<UniversityContextValue | null>(null)

export function UniversityProvider({ children }: { children: ReactNode }) {
  const [university, setUniversity] = useState<UniversityName>(
    () => readStorage(STORAGE_KEY, isUniversityName) ?? DEFAULT_UNIVERSITY,
  )

  useEffect(() => {
    writeStorage(STORAGE_KEY, university)
  }, [university])

  return <UniversityContext value={{ university, setUniversity }}>{children}</UniversityContext>
}

export function useUniversity(): UniversityContextValue {
  const value = useContext(UniversityContext)
  if (!value) throw new Error('useUniversity는 UniversityProvider 안에서만 사용할 수 있습니다.')
  return value
}
