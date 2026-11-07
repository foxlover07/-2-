// FastAPI(BE) 주소. VITE_API_URL 이 비어 있으면 null (로컬 데이터만 쓴다).
export const API_URL: string | null = import.meta.env.VITE_API_URL?.trim().replace(/\/+$/, '') || null

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// FastAPI 오류 응답의 detail (문자열 또는 검증 오류 배열) → 사람이 읽을 문장
function detailMessage(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const msgs = detail.flatMap((d) =>
        typeof d === 'object' && d !== null && 'msg' in d ? [String((d as { msg: unknown }).msg)] : [],
      )
      if (msgs.length > 0) return msgs.join(' / ')
    }
  }
  return `서버 오류 (${status})`
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  if (API_URL === null) throw new ApiError(0, '서버 주소(VITE_API_URL)가 설정되지 않았습니다.')
  let res: Response
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch (e) {
    if (init?.signal?.aborted) throw e
    throw new ApiError(0, '서버에 연결하지 못했습니다.')
  }
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, detailMessage(body, res.status))
  return body as T
}
