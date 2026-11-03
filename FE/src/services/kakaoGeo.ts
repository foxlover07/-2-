import type { KakaoNamespace } from '../types/kakao'
import { readStorage, writeStorage } from '../utils/storage'

export interface LatLngLiteral {
  lat: number
  lng: number
}

const GEOCODE_STORAGE_KEY = 'home-poc:geocode'
// 카카오 장소 카테고리 코드: 학교
const SCHOOL_CATEGORY = 'SC4'

function isLatLng(value: unknown): value is LatLngLiteral {
  return (
    typeof value === 'object' &&
    value !== null &&
    Number.isFinite((value as LatLngLiteral).lat) &&
    Number.isFinite((value as LatLngLiteral).lng)
  )
}

function isGeocodeRecord(value: unknown): value is Record<string, LatLngLiteral | null> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.values(value).every((v) => v === null || isLatLng(v))
  )
}

function toLatLng(x: string, y: string): LatLngLiteral | null {
  const pos = { lat: Number(y), lng: Number(x) }
  return isLatLng(pos) ? pos : null
}

// 주소 → 좌표 캐시. null은 "검색 결과 없음"으로 확정된 주소다.
// 네트워크 오류(ERROR)는 캐시하지 않아 다음에 다시 시도한다.
const geocodeCache = new Map<string, LatLngLiteral | null>(
  Object.entries(readStorage(GEOCODE_STORAGE_KEY, isGeocodeRecord) ?? {}),
)
const geocodePending = new Map<string, Promise<LatLngLiteral | null>>()

export function geocodeAddress(kakao: KakaoNamespace, address: string): Promise<LatLngLiteral | null> {
  const cached = geocodeCache.get(address)
  if (cached !== undefined) return Promise.resolve(cached)
  const pending = geocodePending.get(address)
  if (pending) return pending

  const request = new Promise<LatLngLiteral | null>((resolve) => {
    const { Geocoder, Status } = kakao.maps.services
    new Geocoder().addressSearch(address, (result, status) => {
      if (status === Status.ERROR) {
        resolve(null)
        return
      }
      const pos = status === Status.OK && result[0] ? toLatLng(result[0].x, result[0].y) : null
      geocodeCache.set(address, pos)
      writeStorage(GEOCODE_STORAGE_KEY, Object.fromEntries(geocodeCache))
      resolve(pos)
    })
  }).finally(() => geocodePending.delete(address))

  geocodePending.set(address, request)
  return request
}

const placeCache = new Map<string, LatLngLiteral | null>()

// 학교 위치는 하드코딩하지 않고 장소 검색으로 찾는다. 메모리에만 캐시한다.
export function searchSchool(kakao: KakaoNamespace, keyword: string): Promise<LatLngLiteral | null> {
  const cached = placeCache.get(keyword)
  if (cached !== undefined) return Promise.resolve(cached)

  return new Promise((resolve) => {
    const { Places, Status } = kakao.maps.services
    new Places().keywordSearch(
      keyword,
      (result, status) => {
        if (status === Status.ERROR) {
          resolve(null)
          return
        }
        const pos = status === Status.OK && result[0] ? toLatLng(result[0].x, result[0].y) : null
        placeCache.set(keyword, pos)
        resolve(pos)
      },
      { category_group_code: SCHOOL_CATEGORY },
    )
  })
}
