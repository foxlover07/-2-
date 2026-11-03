// 대학 선택 목록. name은 매물 데이터의 university 값, 학교 위치 검색 키워드로 쓴다.
// 현재 매물 데이터는 이화여자대학교만 있다.
export const UNIVERSITIES = [
  { label: '이화여대', name: '이화여자대학교' },
  { label: '서울과기대', name: '서울과학기술대학교' },
  { label: '고려대', name: '고려대학교' },
  { label: '서강대', name: '서강대학교' },
] as const

export type UniversityName = (typeof UNIVERSITIES)[number]['name']

export const DEFAULT_UNIVERSITY: UniversityName = '이화여자대학교'

export function isUniversityName(value: unknown): value is UniversityName {
  return UNIVERSITIES.some((u) => u.name === value)
}

export function getUniversityLabel(name: UniversityName): string {
  return UNIVERSITIES.find((u) => u.name === name)?.label ?? name
}
