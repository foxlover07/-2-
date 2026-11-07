// 추천(BE app/categories.py, app/scoring_config.py)과 똑같이 맞춰야 하는 상수.
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

// 자주 가는 지역. '자주 가는 곳' 카테고리 데이터가 준비되기 전까지는 저장만 하고 계산에 쓰지 않는다.
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

// ---------------------------------------------------------------- 추천 v3 (BE app/categories.py)

// 카테고리 9개. 사용자가 1~3개를 순서대로 고른다. ready=false 는 데이터 준비 중 (골라도 점수 없음, 비중은 나머지에 분배).
export const CATEGORIES = [
  { key: 'weekly_commute', no: 1, group: '동선', label: '주간 총 통학 시간', short: '주간 통학', description: '요일별 첫·마지막 수업 건물 ↔ 집, 일주일 치 합산', ready: true },
  { key: 'gap_home', no: 2, group: '동선', label: '공강 귀가 가능 여부', short: '공강 귀가', description: '공강 > 왕복 + 쉬는 시간인 공강 비율', ready: true },
  { key: 'frequent_places', no: 3, group: '동선', label: '자주 가는 곳까지 거리', short: '자주 가는 곳', description: '알바·헬스장·본가 가는 역 등 × 주간 횟수', ready: false },
  { key: 'late_return', no: 4, group: '동선', label: '심야 귀가 동선', short: '심야 귀가', description: '늦게 끝나는 일정 → 집 도보 시간 (안전시설은 표시만)', ready: false },
  { key: 'first_class', no: 5, group: '동선', label: '1교시 부담', short: '1교시', description: '9시 수업 날 집에서 나와야 하는 시각', ready: true },
  { key: 'uphill', no: 6, group: '동선', label: '오르막 부담', short: '오르막', description: '매물 → 강의 건물 고도차', ready: false },
  { key: 'amenities', no: 7, group: '생활 환경', label: '생활 편의시설', short: '편의시설', description: '편의점·마트·코인세탁소·병원 거리', ready: false },
  { key: 'quietness', no: 8, group: '생활 환경', label: '조용함 vs 번화함', short: '조용함', description: '반경 내 술집·유흥시설 수', ready: false },
  { key: 'rhythm', no: 9, group: '생활 환경', label: '생활 리듬', short: '생활 리듬', description: '아침형·야행성과 늦게 여는 시설', ready: false },
] as const
export type CategoryKey = (typeof CATEGORIES)[number]['key']

export const MAX_CATEGORIES = 3
export const DEFAULT_CATEGORIES: CategoryKey[] = ['weekly_commute', 'gap_home', 'first_class']

export function categoryInfo(key: string) {
  return CATEGORIES.find((c) => c.key === key)
}

// 1·2·3순위 가중치 (scoring_config.weight_rank1~3) — 화면 안내용. 사용자에게 받지 않는다.
export const RANK_WEIGHTS = [0.55, 0.3, 0.15] as const

// 고른 카테고리의 질문 (BE categories.Answers 기본값과 같게)
export const ANSWER_FIELDS = {
  weekly_commute: { field: 'weeklyCommuteTargetMin', question: '일주일 통학, 어느 정도면 괜찮아요? (도보 기준)', help: '이 시간 안이면 완만하게, 넘으면 점수가 빠르게 줄어듭니다', unit: '분', prefix: '주 ', min: 30, max: 600, step: 10, default: 150, example: '월·수 9시 공과대학, 목 10시 30분 학관 · 일주일 150분 이내면 괜찮아요' },
  gap_home: { field: 'gapRestMin', question: '공강에 집에서 최소 얼마나 쉬어야 다녀올 만한가요?', help: '공강 > 건물→집 + 쉬는 시간 + 집→다음 건물이면 "귀가 가능"으로 셉니다 · 시간표 필요', unit: '분', prefix: '', min: 0, max: 120, step: 10, default: 30, example: '30분 — 1시간 30분 공강이면 집에서 30분은 쉴 수 있어야 해요' },
  first_class: { field: 'firstClassBufferMin', question: '9시 수업 전, 도착 여유는 몇 분이면 좋아요?', help: '9시까지 시작하는 수업이 있는 날 "몇 시에 나가야 하는지"로 계산합니다 · 시간표 필요', unit: '분', prefix: '', min: 0, max: 60, step: 5, default: 10, example: '10분 — 강의실에 8시 50분까지 도착하고 싶어요' },
} as const
export type AnswerField = (typeof ANSWER_FIELDS)[keyof typeof ANSWER_FIELDS]['field']
export type Answers = Record<AnswerField, number>
export const DEFAULT_ANSWERS: Answers = { weeklyCommuteTargetMin: 150, gapRestMin: 30, firstClassBufferMin: 10 }

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

// 예산·보증금 예산은 1차 필터로만 쓰고 점수에는 쓰지 않는다 (BE scoring_config, 2026-11-08 결정: 여유 0)
