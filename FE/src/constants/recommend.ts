// 추천 v2(BE app/scoring.py, app/scoring_config.py)와 똑같이 맞춰야 하는 상수.
// BE 값이 바뀌면 이 파일만 고친다.

export const DAYS = ['월', '화', '수', '목', '금', '토', '일'] as const
export type Day = (typeof DAYS)[number]

// HH:MM (00:00~23:59). <input type="time"> 값과 같은 형식.
export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

// 22:00 이후 또는 05:00 이전에 끝나는 일정은 심야 일정으로 표시만 한다 (추천 v2 계산에는 쓰지 않음).
export const LATE_NIGHT_FROM = '22:00'
export const LATE_NIGHT_UNTIL = '05:00'

export function isLateNight(hhmm: string): boolean {
  return hhmm >= LATE_NIGHT_FROM || hhmm < LATE_NIGHT_UNTIL
}

// 자주 가는 지역. 추천 v2 10장 7단계(자주 가는 곳 + 대중교통 추정) 전까지는 저장만 하고 계산에 쓰지 않는다.
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

export const PLACE_KINDS = [
  { value: 'work', label: '알바' },
  { value: 'gym', label: '헬스장' },
  { value: 'study', label: '공부' },
  { value: 'home_town', label: '본가' },
  { value: 'etc', label: '기타' },
] as const
export type PlaceKind = (typeof PLACE_KINDS)[number]['value']

// 주간 횟수 (하루 2번 × 7일)
export const VISITS_MIN = 1
export const VISITS_MAX = 14

// ---------------------------------------------------------------- 추천 v2

// 요소 (scoring.FACTORS / FACTOR_LABELS). 순서는 화면 표시 순서.
export const FACTORS = [
  { key: 'travel', label: '이동시간', description: '학과 건물 · 시간표 건물까지 도보 (편도)' },
  { key: 'transit', label: '교통 편의성', description: '가장 가까운 지하철역까지 도보' },
  { key: 'cost', label: '주거비', description: '월세 + 관리비' },
] as const
export type Factor = (typeof FACTORS)[number]['key']
export type Priorities = [Factor, Factor]

export const DEFAULT_PRIORITIES: Priorities = ['travel', 'cost']

// 데이터가 아직 없어 BE 가 계산하지 못하는 요소 (고르면 비중이 나머지 요소에 나눠진다)
export const DATA_PENDING_FACTORS: readonly Factor[] = ['transit']

export function factorLabel(key: string): string {
  return FACTORS.find((f) => f.key === key)?.label ?? key
}

// 1·2·3순위 가중치 (scoring_config.weight_rank1~3) — 화면 안내용
export const RANK_WEIGHTS = [0.55, 0.3, 0.15] as const

// 이동시간 세부 부분 (scoring.TRAVEL_PART_LABELS)
export const TRAVEL_PART_LABELS: Record<string, string> = {
  department: '학과 건물',
  frequent: '자주 가는 곳',
  timetable: '시간표',
}

// 학과 → 학과 건물 (scoring_config.DEPARTMENT_BUILDINGS).
// ⚠️ BE 와 같은 예시값, 팀 확인 필요. 목록에 없는 학과는 건물을 직접 고른다.
export const DEPARTMENT_BUILDINGS: Record<string, readonly string[]> = {
  컴퓨터공학전공: ['공과대학'],
  경영학부: ['이화·포스코관'],
  국어국문학과: ['학관', 'ECC'],
}
export const DEPARTMENTS = Object.keys(DEPARTMENT_BUILDINGS)

// 학년 (BE 는 1 이상만 검사. 화면 선택지는 1~5)
export const GRADES = [1, 2, 3, 4, 5] as const

// 희망 통학 시간 (편도, 도보 기준, 분). BE 는 0 초과만 검사.
export const COMMUTE_DEFAULT = 15
export const COMMUTE_MIN = 5
export const COMMUTE_MAX = 60
export const COMMUTE_STEP = 5

// 예산 여유 (scoring_config.monthly_budget_slack_manwon / deposit_budget_slack_ratio) — 화면 안내용
export const MONTHLY_BUDGET_SLACK_MANWON = 10
export const DEPOSIT_BUDGET_SLACK_PERCENT = 20
