import { useEffect, useRef, useState } from 'react'
import { geocodeAddress, searchSchool, type LatLngLiteral } from '../../services/kakaoGeo'
import type { KakaoCustomOverlay, KakaoMapInstance, KakaoMarkerClusterer, KakaoNamespace } from '../../types/kakao'
import type { Property } from '../../types/property'
import { getMonthlyFixedCost } from '../../utils/cost'
import { formatManwonShort } from '../../utils/format'
import { MapPlaceholder } from './MapPlaceholder'

export type LocationStatus = 'ok' | 'failed'

interface KakaoMapProps {
  kakao: KakaoNamespace
  properties: Property[]
  selectedId: string | null
  onSelect: (id: string) => void
  university: string
  onLocationStatusChange?: (status: Record<string, LocationStatus>) => void
}

interface PropertyMarker {
  overlay: KakaoCustomOverlay
  el: HTMLButtonElement
  pos: LatLngLiteral
}

const SCHOOL_LEVEL = 5
// 이 레벨부터(더 넓게 보면) 가까운 매물을 숫자 묶음으로 보여 준다. 매물이 수백 개라 가격 라벨이 겹치지 않게.
const CLUSTER_MIN_LEVEL = 5

// 데이터에 좌표가 있으면 그대로 쓰고(파이프라인에서 Kakao 주소 검색으로 확정한 지번 좌표), 없을 때만 지오코딩한다.
async function locate(kakao: KakaoNamespace, p: Property): Promise<LatLngLiteral | null> {
  if (p.latitude !== null && p.longitude !== null) return { lat: p.latitude, lng: p.longitude }
  return geocodeAddress(kakao, p.address)
}
const MARKER_BASE =
  'cursor-pointer whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-bold shadow-md transition-colors'
const MARKER_NORMAL = 'border-blue bg-white text-blue hover:bg-blue-50'
const MARKER_SELECTED = 'border-navy bg-navy text-white'

function applyMarkerStyle(marker: PropertyMarker, selected: boolean) {
  marker.el.className = `${MARKER_BASE} ${selected ? MARKER_SELECTED : MARKER_NORMAL}`
  marker.el.setAttribute('aria-pressed', String(selected))
  marker.overlay.setZIndex(selected ? 10 : 1)
}

export function KakaoMap({ kakao, properties, selectedId, onSelect, university, onLocationStatusChange }: KakaoMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<KakaoMapInstance | null>(null)
  const markersRef = useRef(new Map<string, PropertyMarker>())
  const schoolOverlayRef = useRef<KakaoCustomOverlay | null>(null)
  const clustererRef = useRef<KakaoMarkerClusterer | null>(null)
  const centeredForRef = useRef<string | null>(null)

  // 콜백·선택값은 ref로 들고 있어 지도를 다시 그리지 않고 최신 값을 쓴다.
  const onSelectRef = useRef(onSelect)
  const onStatusRef = useRef(onLocationStatusChange)
  const selectedIdRef = useRef(selectedId)
  useEffect(() => {
    onSelectRef.current = onSelect
    onStatusRef.current = onLocationStatusChange
    selectedIdRef.current = selectedId
  })

  const [notice, setNotice] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  // 학교 검색 + 매물 지오코딩 후 마커를 다시 그린다.
  useEffect(() => {
    let cancelled = false
    const { LatLng, LatLngBounds, Map: KakaoMapClass, CustomOverlay } = kakao.maps
    const toKakao = (p: LatLngLiteral) => new LatLng(p.lat, p.lng)

    async function render() {
      const [school, located] = await Promise.all([
        searchSchool(kakao, university),
        Promise.all(properties.map(async (p) => ({ p, pos: await locate(kakao, p) }))),
      ])
      if (cancelled || !containerRef.current) return

      onStatusRef.current?.(
        Object.fromEntries(located.map(({ p, pos }) => [p.property_id, pos ? 'ok' : 'failed'] as const)),
      )

      clustererRef.current?.clear()
      markersRef.current.forEach((m) => m.overlay.setMap(null))
      markersRef.current.clear()
      schoolOverlayRef.current?.setMap(null)
      schoolOverlayRef.current = null

      const firstPos = school ?? located.find(({ pos }) => pos)?.pos
      if (!mapRef.current) {
        // 좌표를 하드코딩하지 않기 위해, 검색된 위치가 하나라도 있을 때 지도를 만든다.
        if (!firstPos) {
          setUnavailable(true)
          return
        }
        mapRef.current = new KakaoMapClass(containerRef.current, { center: toKakao(firstPos), level: SCHOOL_LEVEL })
      }
      const map = mapRef.current
      setUnavailable(false)
      const { MarkerClusterer } = kakao.maps
      if (!clustererRef.current && MarkerClusterer) {
        clustererRef.current = new MarkerClusterer({ map, averageCenter: true, minLevel: CLUSTER_MIN_LEVEL, gridSize: 60 })
      }

      if (school) {
        const el = document.createElement('div')
        el.className = 'rounded-control bg-navy px-3 py-1.5 text-xs font-bold text-white shadow-lg'
        el.textContent = university
        schoolOverlayRef.current = new CustomOverlay({ position: toKakao(school), content: el, yAnchor: 1.3, zIndex: 5 })
        schoolOverlayRef.current.setMap(map)
      }

      for (const { p, pos } of located) {
        if (!pos) continue
        const el = document.createElement('button')
        el.type = 'button'
        el.textContent = formatManwonShort(getMonthlyFixedCost(p))
        el.setAttribute('aria-label', `${p.property_id} 매물 선택`)
        el.addEventListener('click', () => onSelectRef.current(p.property_id))
        const overlay = new CustomOverlay({ position: toKakao(pos), content: el, yAnchor: 1, clickable: true })
        const marker = { overlay, el, pos }
        applyMarkerStyle(marker, p.property_id === selectedIdRef.current)
        if (!clustererRef.current) overlay.setMap(map)
        markersRef.current.set(p.property_id, marker)
      }
      clustererRef.current?.addMarkers([...markersRef.current.values()].map((m) => m.overlay))

      // 대학이 바뀌었을 때만 화면 위치를 다시 잡는다. 필터 변경으로는 이동하지 않는다.
      if (centeredForRef.current !== university) {
        centeredForRef.current = university
        if (school) {
          map.setLevel(SCHOOL_LEVEL)
          map.setCenter(toKakao(school))
          setNotice(null)
        } else {
          const positions = located.flatMap(({ pos }) => (pos ? [pos] : []))
          if (positions.length > 0) {
            const bounds = new LatLngBounds()
            positions.forEach((pos) => bounds.extend(toKakao(pos)))
            map.setBounds(bounds)
          }
          setNotice(`'${university}' 위치를 찾지 못해 매물 전체가 보이도록 표시합니다.`)
        }
      }
    }

    render().catch(() => {
      if (!cancelled) setNotice('지도 정보를 불러오는 중 오류가 발생했습니다.')
    })
    return () => {
      cancelled = true
    }
  }, [kakao, university, properties])

  // 선택 변경: 마커 강조 + 해당 위치로 이동
  useEffect(() => {
    markersRef.current.forEach((m, id) => applyMarkerStyle(m, id === selectedId))
    const target = selectedId ? markersRef.current.get(selectedId) : undefined
    if (target && mapRef.current) mapRef.current.panTo(new kakao.maps.LatLng(target.pos.lat, target.pos.lng))
  }, [kakao, selectedId])

  // 레이아웃 크기가 바뀌면 지도 타일을 다시 계산한다.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver(() => mapRef.current?.relayout())
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      {unavailable && (
        <div className="absolute inset-0">
          <MapPlaceholder message="학교와 매물 위치를 찾지 못해 지도를 표시할 수 없습니다." />
        </div>
      )}
      {notice && !unavailable && (
        <p className="absolute top-3 left-1/2 z-10 -translate-x-1/2 rounded-control bg-white/95 px-3 py-1.5 text-xs text-muted shadow">
          {notice}
        </p>
      )}
    </div>
  )
}
