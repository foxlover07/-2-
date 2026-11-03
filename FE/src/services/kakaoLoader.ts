import type { KakaoNamespace } from '../types/kakao'

const LOAD_TIMEOUT_MS = 10_000

let loading: Promise<KakaoNamespace> | null = null

// SDK 스크립트를 한 번만 동적으로 붙이고, autoload=false이므로 kakao.maps.load로 초기화한다.
export function loadKakaoMaps(appKey: string): Promise<KakaoNamespace> {
  if (loading) return loading

  loading = new Promise<KakaoNamespace>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error('카카오맵 SDK 로드 시간이 초과되었습니다.')), LOAD_TIMEOUT_MS)
    const done = (fn: () => void) => {
      window.clearTimeout(timer)
      fn()
    }

    const script = document.createElement('script')
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(appKey)}&autoload=false&libraries=services`
    script.async = true
    script.onload = () => {
      const kakao = window.kakao
      if (!kakao?.maps) {
        done(() => reject(new Error('카카오맵 SDK를 초기화하지 못했습니다.')))
        return
      }
      kakao.maps.load(() => done(() => resolve(kakao)))
    }
    script.onerror = () => done(() => reject(new Error('카카오맵 SDK 스크립트를 불러오지 못했습니다.')))
    document.head.appendChild(script)
  })

  // 실패하면 다음 호출에서 다시 시도할 수 있게 한다.
  loading.catch(() => {
    loading = null
  })
  return loading
}
