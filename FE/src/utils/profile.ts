import {
  DAYS,
  DEFAULT_LARGER_AREA,
  EWHA_AREAS,
  LARGER_AREA_OPTIONS,
  TIME_RE,
  isFactor,
  type Area,
  type Day,
  type Factor,
  type LargerArea,
} from '../constants/recommend'
import type { SpotVisits } from './walk'

export interface ClassSlot {
  day: Day
  start: string // HH:MM
  end: string // HH:MM
  building: string // 도보시간 데이터의 건물 이름
}

// 자주 가는 곳 (통합안 1.1: 장소, 주간 횟수). 데이터 준비 중이라 저장만 하고 계산에 쓰지 않는다.
export interface FrequentPlace {
  area: Area
  visitsPerWeek: number
}

export interface Profile {
  // 추천 입력 (BE scoring.RecommendRequest, 추천 시스템 통합안). 대학은 UniversityContext 에 따로 저장한다.
  department: string // '' = 미입력
  departmentBuildings: string[]
  grade: number | null
  priorities: Factor[] // 1·2순위 순서 (0~2개, 추천에는 2개 필요). 3순위는 자동
  budgetManwon: number | null // 유지비(월세 + 관리비) 상한. 필수
  depositBudgetManwon: number | null // 보증금 상한. 선택
  desiredCommuteMin: number | null // 희망 통학 시간 (편도, 도보). 필수
  desiredAreaM2: number | null // 희망 면적. 필수
  largerArea: LargerArea // 더 넓은 면적: 좋음 / 상관없음 / 싫음
  timetable: ClassSlot[]
  places: FrequentPlace[]
  // 마지막 저장 시각 (ISO). 저장한 적이 없으면 null.
  savedAt: string | null
}

export const EMPTY_PROFILE: Profile = {
  department: '',
  departmentBuildings: [],
  grade: null,
  priorities: [],
  budgetManwon: null,
  depositBudgetManwon: null,
  desiredCommuteMin: null,
  desiredAreaM2: null,
  largerArea: DEFAULT_LARGER_AREA,
  timetable: [],
  places: [],
  savedAt: null,
}

// ---------------------------------------------------------------- 검증

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v)
const isTime = (v: unknown): v is string => typeof v === 'string' && TIME_RE.test(v)
const isNonNegOrNull = (v: unknown): v is number | null =>
  v === null || (typeof v === 'number' && Number.isFinite(v) && v >= 0)
const isPositive = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0

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

// 예전 형식(용도·끝나는 시각·학교에서 바로 가는지 포함)도 장소와 횟수만 읽는다
function toPlace(v: unknown): FrequentPlace[] {
  if (!isObj(v) || !EWHA_AREAS.includes(v.area as Area) || !isInt(v.visitsPerWeek) || v.visitsPerWeek < 0) return []
  return [{ area: v.area as Area, visitsPerWeek: v.visitsPerWeek }]
}

function parsePriorities(v: unknown): Factor[] {
  if (!Array.isArray(v)) return []
  // v2 의 transit(교통 편의성) 같은 없어진 값은 버린다
  return [...new Set(v.filter(isFactor))].slice(0, 2)
}

/**
 * localStorage 값 → Profile. 예전 형식(v3 카테고리·답변·방문 횟수, v2 교통 편의성)도 읽는다.
 * 없거나 틀린 필드는 기본값을 쓰고, 시간표·장소는 올바른 행만 남긴다. 없어진 필드는 버린다.
 */
export function parseStoredProfile(value: unknown): Profile | null {
  if (!isObj(value)) return null
  const v = value
  return {
    department: typeof v.department === 'string' ? v.department : '',
    departmentBuildings: Array.isArray(v.departmentBuildings)
      ? v.departmentBuildings.filter((b): b is string => typeof b === 'string' && b !== '')
      : [],
    grade: isInt(v.grade) && v.grade >= 1 ? v.grade : null,
    priorities: parsePriorities(v.priorities),
    budgetManwon: isNonNegOrNull(v.budgetManwon) ? v.budgetManwon : null,
    depositBudgetManwon: isNonNegOrNull(v.depositBudgetManwon) ? v.depositBudgetManwon : null,
    desiredCommuteMin: isPositive(v.desiredCommuteMin) ? v.desiredCommuteMin : null,
    desiredAreaM2: isPositive(v.desiredAreaM2) ? v.desiredAreaM2 : null,
    largerArea: LARGER_AREA_OPTIONS.some((o) => o.value === v.largerArea) ? (v.largerArea as LargerArea) : DEFAULT_LARGER_AREA,
    timetable: Array.isArray(v.timetable) ? v.timetable.filter(isClassSlot) : [],
    places: Array.isArray(v.places) ? v.places.flatMap(toPlace) : [],
    savedAt: typeof v.savedAt === 'string' ? v.savedAt : null,
  }
}

/** 추천 요청에 필요한 입력이 빠졌으면 그 목록, 다 있으면 빈 배열. */
export function missingForRecommend(p: Profile): string[] {
  const missing: string[] = []
  if (p.department.trim() === '') missing.push('학과')
  if (p.departmentBuildings.length === 0) missing.push('학과 건물')
  if (p.grade === null) missing.push('학년')
  if (p.priorities.length < 2) missing.push('우선순위 1·2순위')
  if (p.budgetManwon === null || p.budgetManwon <= 0) missing.push('유지비 상한')
  if (p.desiredCommuteMin === null) missing.push('희망 통학 시간')
  if (p.desiredAreaM2 === null) missing.push('희망 면적')
  return missing
}

/** 매물 상세·비교 화면의 '학과 건물 평균 도보시간' 계산용. 학과 건물마다 1번씩 센다 (강의동 방문 횟수 입력은 없앴다). */
export function departmentVisits(p: Profile): SpotVisits {
  return Object.fromEntries(p.departmentBuildings.map((b) => [b, 1]))
}
