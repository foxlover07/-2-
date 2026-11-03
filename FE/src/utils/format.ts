export const NO_INFO = '정보 없음'

export function formatManwon(value: number | null): string {
  return value === null ? NO_INFO : `${value.toLocaleString('ko-KR')}만원`
}

// 지도 마커처럼 좁은 곳에 쓰는 짧은 표기.
export function formatManwonShort(value: number | null): string {
  return value === null ? NO_INFO : `${value.toLocaleString('ko-KR')}만`
}

export function formatArea(value: number | null): string {
  return value === null ? NO_INFO : `${value}㎡`
}

export function formatMinutes(value: number | null): string {
  return value === null ? NO_INFO : `${value}분`
}
