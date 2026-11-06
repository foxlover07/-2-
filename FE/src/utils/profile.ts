import {
  CRITERIA,
  DAYS,
  DISABLED_CRITERIA,
  EQUAL_WEIGHTS,
  EWHA_AREAS,
  GAP_BUFFER_DEFAULT,
  GAP_BUFFER_MAX,
  GAP_BUFFER_MIN,
  IMPORTANCE_MAX,
  IMPORTANCE_MIN,
  PLACE_KINDS,
  TIME_RE,
  WEIGHTS_TOTAL,
  type Area,
  type Day,
  type PlaceKind,
  type WeightsPercent,
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
  // 간단 추천 설정 (BE recommend.Profile 과 같은 모양). 선택한 강의동만 키로 들어간다.
  visits: SpotVisits
  budgetManwon: number | null
  costImportance: number
  walkImportance: number
  // 학생 맞춤 추천 (BE criteria.Lifestyle)
  timetable: ClassSlot[]
  places: FrequentPlace[]
  gapBufferMin: number
  weightsPercent: WeightsPercent
  // 마지막 저장 시각 (ISO). 저장한 적이 없으면 null.
  savedAt: string | null
}

export const EMPTY_PROFILE: Profile = {
  visits: {},
  budgetManwon: null,
  costImportance: 3,
  walkImportance: 3,
  timetable: [],
  places: [],
  gapBufferMin: GAP_BUFFER_DEFAULT,
  weightsPercent: EQUAL_WEIGHTS,
  savedAt: null,
}

// ---------------------------------------------------------------- 검증

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v)
const isTime = (v: unknown): v is string => typeof v === 'string' && TIME_RE.test(v)

export function isImportance(v: unknown): v is number {
  return isInt(v) && v >= IMPORTANCE_MIN && v <= IMPORTANCE_MAX
}

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

export function weightsTotal(w: WeightsPercent): number {
  return CRITERIA.reduce((sum, c) => sum + w[c.key], 0)
}

function isWeightsPercent(v: unknown): v is WeightsPercent {
  if (!isObj(v)) return false
  const ok = CRITERIA.every((c) => {
    const n = v[c.key]
    return typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= WEIGHTS_TOTAL
  })
  if (!ok) return false
  const w = v as WeightsPercent
  return DISABLED_CRITERIA.every((k) => w[k] === 0) && Math.abs(weightsTotal(w) - WEIGHTS_TOTAL) <= 0.5
}

// 간단 추천 설정은 예전 저장값에도 있던 필드라 하나라도 틀리면 저장값 전체를 버린다(기존 동작과 같음).
function hasLegacyFields(p: Obj): boolean {
  const visits = p.visits
  return (
    isObj(visits) &&
    Object.values(visits).every((n) => isInt(n) && n >= 0) &&
    (p.budgetManwon === null || (typeof p.budgetManwon === 'number' && p.budgetManwon >= 0)) &&
    isImportance(p.costImportance) &&
    isImportance(p.walkImportance)
  )
}

/**
 * localStorage 값 → Profile. 예전 형식(새 필드 없음)도 읽는다.
 * 새 필드는 없거나 틀리면 기본값을 쓰고, 시간표·장소는 올바른 행만 남긴다.
 */
export function parseStoredProfile(value: unknown): Profile | null {
  if (!isObj(value) || !hasLegacyFields(value)) return null
  const gap = value.gapBufferMin
  return {
    visits: value.visits as SpotVisits,
    budgetManwon: value.budgetManwon as number | null,
    costImportance: value.costImportance as number,
    walkImportance: value.walkImportance as number,
    timetable: Array.isArray(value.timetable) ? value.timetable.filter(isClassSlot) : [],
    places: Array.isArray(value.places) ? value.places.filter(isFrequentPlace) : [],
    gapBufferMin: isInt(gap) && gap >= GAP_BUFFER_MIN && gap <= GAP_BUFFER_MAX ? gap : GAP_BUFFER_DEFAULT,
    weightsPercent: isWeightsPercent(value.weightsPercent) ? value.weightsPercent : EQUAL_WEIGHTS,
    savedAt: typeof value.savedAt === 'string' ? value.savedAt : null,
  }
}
