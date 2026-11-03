import { useEffect, useState } from 'react'
import { loadKakaoMaps } from '../../services/kakaoLoader'
import type { KakaoNamespace } from '../../types/kakao'
import type { Property } from '../../types/property'
import { KakaoMap, type LocationStatus } from './KakaoMap'
import { MapPlaceholder } from './MapPlaceholder'

export type { LocationStatus }

export interface MapViewProps {
  properties: Property[]
  selectedId: string | null
  onSelect: (id: string) => void
  // 학교 위치 검색 키워드(대학 이름)
  university: string
  // 매물별 좌표 변환 결과. 카드의 "위치 확인 불가" 표시에 쓴다.
  onLocationStatusChange?: (status: Record<string, LocationStatus>) => void
}

const APP_KEY = import.meta.env.VITE_KAKAO_MAP_KEY?.trim() ?? ''

type SdkState = { kind: 'loading' } | { kind: 'ready'; kakao: KakaoNamespace } | { kind: 'error'; message: string }

export function MapView(props: MapViewProps) {
  const [sdk, setSdk] = useState<SdkState>({ kind: 'loading' })

  useEffect(() => {
    if (!APP_KEY) return
    let cancelled = false
    loadKakaoMaps(APP_KEY)
      .then((kakao) => !cancelled && setSdk({ kind: 'ready', kakao }))
      .catch((e: unknown) => !cancelled && setSdk({ kind: 'error', message: e instanceof Error ? e.message : String(e) }))
    return () => {
      cancelled = true
    }
  }, [])

  if (!APP_KEY) {
    return (
      <MapPlaceholder
        message="지도 키가 설정되지 않았습니다."
        detail="FE/.env.local에 VITE_KAKAO_MAP_KEY를 넣으면 카카오맵이 표시됩니다."
      />
    )
  }
  if (sdk.kind === 'loading') return <MapPlaceholder message="지도를 불러오는 중입니다…" />
  if (sdk.kind === 'error') {
    return (
      <MapPlaceholder
        message={sdk.message}
        detail="앱 키와 카카오 개발자 콘솔의 플랫폼(Web) 사이트 도메인 등록을 확인해 주세요."
      />
    )
  }
  return <KakaoMap kakao={sdk.kakao} {...props} />
}
