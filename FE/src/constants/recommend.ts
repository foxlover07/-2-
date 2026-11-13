// 추천(BE app/scoring.py, app/scoring_config.py — 추천 시스템 통합안 2026-11-13)과 똑같이 맞춰야 하는 상수.
// BE 값이 바뀌면 이 파일만 고친다.

export const DAYS = ['월', '화', '수', '목', '금', '토', '일'] as const
export type Day = (typeof DAYS)[number]

// HH:MM (00:00~23:59). <input type="time"> 값과 같은 형식.
export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/

// 자주 가는 곳. 데이터 준비 중이라 저장만 하고 계산에 쓰지 않는다 (통합안 1.1).
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

// 주간 횟수 (하루 2번 × 7일)
export const VISITS_MIN = 1
export const VISITS_MAX = 14

// ---------------------------------------------------------------- 요소 3개와 우선순위 (BE scoring.FACTORS)

export const FACTORS = [
  { key: 'cost', label: '예산', description: '유지비(월세 + 관리비)가 상한보다 얼마나 싼지' },
  { key: 'travel', label: '이동 시간', description: '학과 건물·시간표 건물까지 편도 도보 시간' },
  { key: 'area', label: '면적', description: '희망 면적과 비교 (넓은 면적 선호에 따라)' },
] as const
export type Factor = (typeof FACTORS)[number]['key']

export function factorLabel(key: string): string {
  return FACTORS.find((f) => f.key === key)?.label ?? key
}

export function isFactor(v: unknown): v is Factor {
  return FACTORS.some((f) => f.key === v)
}

// 1·2·3순위 가중치 (scoring_config.weight_rank1~3) — 화면 안내용. 사용자에게 받지 않는다.
export const RANK_WEIGHTS = [0.55, 0.3, 0.15] as const

// 희망보다 넓은 면적 (BE RecommendRequest.larger_area)
export const LARGER_AREA_OPTIONS = [
  { value: 'like', label: '좋음', help: '넓을수록 점수가 오릅니다' },
  { value: 'neutral', label: '상관없음', help: '희망 면적 이상이면 같은 점수' },
  { value: 'dislike', label: '싫음', help: '넓을수록 조금씩 점수가 내려갑니다' },
] as const
export type LargerArea = (typeof LARGER_AREA_OPTIONS)[number]['value']
export const DEFAULT_LARGER_AREA: LargerArea = 'neutral'

// 필터 여유 (scoring_config) — 화면 안내용
export const MONTHLY_BUDGET_SLACK_MANWON = 10 // 유지비 > 상한 + 10만 원 → 제외, 그 사이는 '예산 초과' 표시
export const DEPOSIT_BUDGET_SLACK_RATIO = 0.2 // 보증금 > 상한 × 1.2 → 제외

// 추천 결과에서 강조하는 상위 개수 (scoring_config.displayed_top_n)
export const DISPLAYED_TOP_N = 3

// 학과 → 학과 건물 (scoring_config.DEPARTMENT_BUILDINGS, 이대만).
// ⚠️ BE 와 같은 예시값, 팀 확인 필요. 목록에 없는 학과는 건물을 직접 고른다.
export const DEPARTMENT_BUILDINGS: Record<string, readonly string[]> = {
  컴퓨터공학전공: ['공과대학'],
  경영학부: ['이화·포스코관'],
  국어국문학과: ['학관', 'ECC'],
}
export const DEPARTMENTS = Object.keys(DEPARTMENT_BUILDINGS)

// 학년 (BE 는 1 이상만 검사. 화면 선택지는 1~5)
export const GRADES = [1, 2, 3, 4, 5] as const
