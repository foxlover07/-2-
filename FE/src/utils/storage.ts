// localStorage 접근은 실패할 수 있다(사생활 보호 모드, 저장 공간 부족 등).
// 실패해도 화면은 기본값으로 동작해야 하므로 모든 접근을 try/catch로 감싼다.

export function readStorage<T>(key: string, isValid: (value: unknown) => value is T): T | null {
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return null
    const value: unknown = JSON.parse(raw)
    return isValid(value) ? value : null
  } catch {
    return null
  }
}

export function writeStorage(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 저장에 실패해도 메모리 상태로 계속 동작한다.
  }
}
