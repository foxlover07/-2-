// 조건 기반 추천(BE app/criteria.py, app/recommend.py)과 똑같이 맞춰야 하는 상수.
// BE 값이 바뀌면 이 파일만 고친다.

export const DAYS = ['월', '화', '수', '목', '금', '토', '일'] as const
export type Day = (typeof DAYS)[number]

// HH:MM (00:00~23:59). <input type="time"> 값과 같은 형식.
export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

// 22:00 이후 또는 05:00 이전에 끝나는 일정은 심야 귀가로 본다 (criteria.LATE_NIGHT_FROM / UNTIL).
export const LATE_NIGHT_FROM = '22:00'
export const LATE_NIGHT_UNTIL = '05:00'

export function isLateNight(hhmm: string): boolean {
  return hhmm >= LATE_NIGHT_FROM || hhmm < LATE_NIGHT_UNTIL
}

// 자주 가는 지역 (criteria.EWHA_AREAS)
export const EWHA_AREAS = [
  '이대역',
  '신촌역',
  '홍대입구역',
  '아현역',
  '공덕역',
  '서울역',
  '광화문·시청',
  '여의도',
] as const
export type Area = (typeof EWHA_AREAS)[number]

// 용도: 화면 이름 → BE 값 (criteria.FrequentPlace.kind)
export const PLACE_KINDS = [
  { value: 'work', label: '알바' },
  { value: 'gym', label: '헬스장' },
  { value: 'study', label: '공부' },
  { value: 'home_town', label: '본가' },
  { value: 'etc', label: '기타' },
] as const
export type PlaceKind = (typeof PLACE_KINDS)[number]['value']

// 공강 여유 시간 (criteria.DEFAULT_GAP_BUFFER_MIN)
export const GAP_BUFFER_DEFAULT = 30
export const GAP_BUFFER_MIN = 0
export const GAP_BUFFER_MAX = 120
export const GAP_BUFFER_STEP = 10

// 주간 횟수 (하루 2번 × 7일)
export const VISITS_MIN = 1
export const VISITS_MAX = 14

// 간단 추천 중요도 (recommend.IMPORTANCE_MIN / MAX)
export const IMPORTANCE_MIN = 1
export const IMPORTANCE_MAX = 5

// 기준 키와 이름 (criteria.CRITERIA). 순서는 화면 표시 순서.
export const CRITERIA = [
  { key: 'monthly_cost', label: '월 고정 주거비', description: '월세 + 관리비' },
  { key: 'weekly_commute', label: '주간 총 통학 시간', description: '시간표의 요일별 첫·마지막 수업 건물 ↔ 집' },
  { key: 'gap_home', label: '공강 귀가 가능 횟수', description: '시간표 + 공강 여유 시간' },
  { key: 'frequent_place', label: '자주 가는 곳 이동시간', description: '알바 포함 · 학교에서 바로 가면 편도' },
  { key: 'late_return', label: '심야 귀가 시간', description: '22:00 이후 끝나는 일정 → 집' },
  { key: 'preference_match', label: '선호 적합도', description: '매물 특성 데이터 필요' },
] as const
export type CriterionKey = (typeof CRITERIA)[number]['key']
export type WeightsPercent = Record<CriterionKey, number>

// 매물 → 지역 이동시간 데이터가 아직 없어 서버가 계산에서 빼는 기준
export const DATA_PENDING_CRITERIA: readonly CriterionKey[] = ['frequent_place', 'late_return']
// 매물 특성 데이터가 없어 0% 고정·비활성인 기준
export const DISABLED_CRITERIA: readonly CriterionKey[] = ['preference_match']

// BE 는 합계 100 ± 0.5 만 통과시킨다.
export const WEIGHTS_TOTAL = 100

// 균등하게 나누기: 선호 적합도를 뺀 5개에 20%씩
export const EQUAL_WEIGHTS: WeightsPercent = {
  monthly_cost: 20,
  weekly_commute: 20,
  gap_home: 20,
  frequent_place: 20,
  late_return: 20,
  preference_match: 0,
}
