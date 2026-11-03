// 이 프로젝트에서 사용하는 카카오맵 JS SDK API만 최소한으로 선언한다.

export interface KakaoLatLng {
  getLat(): number
  getLng(): number
}

export interface KakaoLatLngBounds {
  extend(latlng: KakaoLatLng): void
}

export interface KakaoMapInstance {
  setCenter(latlng: KakaoLatLng): void
  panTo(latlng: KakaoLatLng): void
  setLevel(level: number): void
  setBounds(bounds: KakaoLatLngBounds, paddingTop?: number, paddingRight?: number, paddingBottom?: number, paddingLeft?: number): void
  relayout(): void
}

export interface KakaoCustomOverlay {
  setMap(map: KakaoMapInstance | null): void
  setZIndex(zIndex: number): void
}

export interface KakaoAddressResult {
  x: string
  y: string
  address_name: string
}

export interface KakaoPlaceResult {
  x: string
  y: string
  place_name: string
}

export type KakaoStatus = string

export interface KakaoNamespace {
  maps: {
    load(callback: () => void): void
    LatLng: new (lat: number, lng: number) => KakaoLatLng
    LatLngBounds: new () => KakaoLatLngBounds
    Map: new (container: HTMLElement, options: { center: KakaoLatLng; level?: number }) => KakaoMapInstance
    CustomOverlay: new (options: {
      position: KakaoLatLng
      content: HTMLElement | string
      xAnchor?: number
      yAnchor?: number
      zIndex?: number
      clickable?: boolean
    }) => KakaoCustomOverlay
    services: {
      Geocoder: new () => {
        addressSearch(address: string, callback: (result: KakaoAddressResult[], status: KakaoStatus) => void): void
      }
      Places: new () => {
        keywordSearch(
          keyword: string,
          callback: (result: KakaoPlaceResult[], status: KakaoStatus) => void,
          options?: { category_group_code?: string },
        ): void
      }
      Status: { OK: KakaoStatus; ZERO_RESULT: KakaoStatus; ERROR: KakaoStatus }
    }
  }
}

declare global {
  interface Window {
    kakao?: KakaoNamespace
  }
}
