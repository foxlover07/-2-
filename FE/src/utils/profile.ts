import {
  COMMUTE_DEFAULT,
  DAYS,
  DEFAULT_PRIORITIES,
  EWHA_AREAS,
  FACTORS,
  PLACE_KINDS,
  TIME_RE,
  type Area,
  type Day,
  type Factor,
  type PlaceKind,
  type Priorities,
} from '../constants/recommend'
import type { SpotVisits } from './walk'

export interface ClassSlot {
  day: Day
  start: string // HH:MM
  end: string // HH:MM
  building: string // 매물 데이터의 spotN_name 값
}

export interface FrequentPlace {
  area: Area
  kind: PlaceKind
  visitsPerWeek: number
  fromCampus: boolean // 수업 끝나고 학교에서 바로 가면 true (장소 → 집 편도만 계산)
  endsAt: string | null // HH:MM, 선택
}

export interface Profile {
  // 추천 v2 입력 (BE scoring.RecommendRequest)
  department: string // '' = 미입력
  departmentBuildings: string[]
  grade: number | null
  desiredCommuteMin: number
  budgetManwon: number | null // 월 고정 주거비 예산. 추천 요청에는 필수
  depositBudgetManwon: number | null
  priorities: Priorities
  timetable: ClassSlot[]
  // 자주 가는 곳: 추천 v2 7단계 전까지 저장만 한다
  places: FrequentPlace[]
  // 강의동별 주간 방문 횟수: 매물 상세·비교 화면의 가중 평균 도보시간 표시에 쓴다
  visits: SpotVisits
  // 마지막 저장 시각 (ISO). 저장한 적이 없으면 null.
  savedAt: string | null
}

export const EMPTY_PROFILE: Profile = {
  department: '',
  departmentBuildings: [],
  grade: null,
  desiredCommuteMin: COMMUTE_DEFAULT,
  budgetManwon: null,
  depositBudgetManwon: null,
  priorities: DEFAULT_PRIORITIES,
  timetable: [],
  places: [],
  visits: {},
  savedAt: null,
}

// ---------------------------------------------------------------- 검증

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v)
const isTime = (v: unknown): v is string => typeof v === 'string' && TIME_RE.test(v)
const isNonNegOrNull = (v: unknown): v is number | null =>
  v === null || (typeof v === 'number' && Number.isFinite(v) && v >= 0)
const isFactor = (v: unknown): v is Factor => FACTORS.some((f) => f.key === v)

function isClassSlot(v: unknown): v is ClassSlot {
  return (
    isObj(v) &&
    DAYS.includes(v.day as Day) &&
    isTime(v.start) &&
    isTime(v.end) &&
    typeof v.building === 'string' &&
    v.building !== ''
  )
}

function isFrequentPlace(v: unknown): v is FrequentPlace {
  return (
    isObj(v) &&
    EWHA_AREAS.includes(v.area as Area) &&
    PLACE_KINDS.some((k) => k.value === v.kind) &&
    isInt(v.visitsPerWeek) &&
    v.visitsPerWeek >= 0 &&
    typeof v.fromCampus === 'boolean' &&
    (v.endsAt === null || isTime(v.endsAt))
  )
}

function isPriorities(v: unknown): v is Priorities {
  return Array.isArray(v) && v.length === 2 && isFactor(v[0]) && isFactor(v[1]) && v[0] !== v[1]
}

function isVisits(v: unknown): v is SpotVisits {
  return isObj(v) && Object.values(v).every((n) => isInt(n) && n >= 0)
}

/**
 * localStorage 값 → Profile. 예전 형식(v1: 반영 비율·공강 여유·중요도)도 읽는다.
 * 없거나 틀린 필드는 기본값을 쓰고, 시간표·장소는 올바른 행만 남긴다. v1 전용 필드는 버린다.
 */
export function parseStoredProfile(value: unknown): Profile | null {
  if (!isObj(value)) return null
  const v = value
  const commute = v.desiredCommuteMin
  return {
    department: typeof v.department === 'string' ? v.department : '',
    departmentBuildings: Array.isArray(v.departmentBuildings)
      ? v.departmentBuildings.filter((b): b is string => typeof b === 'string' && b !== '')
      : [],
    grade: isInt(v.grade) && v.grade >= 1 ? v.grade : null,
    desiredCommuteMin: typeof commute === 'number' && commute > 0 ? commute : COMMUTE_DEFAULT,
    budgetManwon: isNonNegOrNull(v.budgetManwon) ? v.budgetManwon : null,
    depositBudgetManwon: isNonNegOrNull(v.depositBudgetManwon) ? v.depositBudgetManwon : null,
    priorities: isPriorities(v.priorities) ? v.priorities : DEFAULT_PRIORITIES,
    timetable: Array.isArray(v.timetable) ? v.timetable.filter(isClassSlot) : [],
    places: Array.isArray(v.places) ? v.places.filter(isFrequentPlace) : [],
    visits: isVisits(v.visits) ? v.visits : {},
    savedAt: typeof v.savedAt === 'string' ? v.savedAt : null,
  }
}

/** 추천 요청에 필요한 입력이 빠졌으면 그 목록, 다 있으면 빈 배열. */
export function missingForRecommend(p: Profile): string[] {
  const missing: string[] = []
  if (p.department.trim() === '') missing.push('학과')
  if (p.departmentBuildings.length === 0) missing.push('학과 건물')
  if (p.grade === null) missing.push('학년')
  if (p.budgetManwon === null || p.budgetManwon <= 0) missing.push('월 주거비 예산')
  return missing
}
