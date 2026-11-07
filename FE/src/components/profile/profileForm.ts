// 생활 프로필 편집 폼 상태 ↔ 저장값(Profile) 변환과 검증. 화면 컴포넌트는 이 파일만 쓴다.
import { DEPARTMENTS, TIME_RE, type Area, type Day, type PlaceKind, type Priorities } from '../../constants/recommend'
import type { ClassSlot, FrequentPlace, Profile } from '../../utils/profile'
import type { SpotVisits } from '../../utils/walk'

export const TIME_INPUT =
  'h-10 w-full rounded-control border bg-white px-3 text-sm text-text outline-none focus:border-blue focus:ring-1 focus:ring-blue'

// 학과 선택지 중 "직접 입력"
export const CUSTOM_DEPARTMENT = '__custom__'

let nextId = 1
export const newRowId = () => nextId++

export interface TimetableRow {
  id: number
  day: Day
  start: string // '' = 미입력
  end: string
  building: string // '' = 미선택
}

export interface PlaceRow {
  id: number
  area: Area
  kind: PlaceKind
  visitsPerWeek: number
  fromCampus: boolean
  endsAt: string // '' = 미입력 (선택)
}

export interface FormState {
  departmentChoice: string // '' = 미선택, DEPARTMENTS 중 하나, 또는 CUSTOM_DEPARTMENT
  customDepartment: string
  departmentBuildings: string[]
  grade: number | null
  desiredCommuteMin: number
  budgetText: string
  depositText: string
  priorities: Priorities
  timetable: TimetableRow[]
  places: PlaceRow[]
  visits: SpotVisits
}

export function toForm(p: Profile): FormState {
  const known = DEPARTMENTS.includes(p.department)
  return {
    departmentChoice: p.department === '' ? '' : known ? p.department : CUSTOM_DEPARTMENT,
    customDepartment: known ? '' : p.department,
    departmentBuildings: p.departmentBuildings,
    grade: p.grade,
    desiredCommuteMin: p.desiredCommuteMin,
    budgetText: p.budgetManwon === null ? '' : String(p.budgetManwon),
    depositText: p.depositBudgetManwon === null ? '' : String(p.depositBudgetManwon),
    priorities: p.priorities,
    timetable: p.timetable.map((s) => ({ id: newRowId(), ...s })),
    places: p.places.map((pl) => ({
      id: newRowId(),
      area: pl.area,
      kind: pl.kind,
      visitsPerWeek: pl.visitsPerWeek,
      fromCampus: pl.fromCampus,
      endsAt: pl.endsAt ?? '',
    })),
    visits: p.visits,
  }
}

export function departmentName(form: FormState): string {
  return (form.departmentChoice === CUSTOM_DEPARTMENT ? form.customDepartment : form.departmentChoice).trim()
}

// 빈 칸은 미설정(null). 숫자가 아니거나 범위를 벗어나면 undefined(오류).
export function parseManwon(text: string, { positive = false } = {}): number | null | undefined {
  if (text.trim() === '') return null
  const n = Number(text)
  if (!Number.isFinite(n)) return undefined
  return (positive ? n > 0 : n >= 0) ? n : undefined
}

// 시간표 한 행의 오류. 없으면 null.
export function timetableRowError(row: TimetableRow, buildings: string[]): string | null {
  if (!TIME_RE.test(row.start) || !TIME_RE.test(row.end)) return '시작·종료 시각을 입력해 주세요.'
  if (row.end <= row.start) return '종료 시각이 시작 시각보다 늦어야 합니다.'
  if (row.building === '') return '강의 건물을 골라 주세요.'
  if (buildings.length > 0 && !buildings.includes(row.building)) return '목록에 없는 건물입니다. 다시 골라 주세요.'
  return null
}

export interface FormCheck {
  budget: number | null | undefined
  budgetError: string | null
  deposit: number | null | undefined
  depositError: string | null
  departmentError: string | null
  rowErrors: Record<number, string>
  canSave: boolean
}

export function checkForm(form: FormState, buildings: string[]): FormCheck {
  const budget = parseManwon(form.budgetText, { positive: true })
  const deposit = parseManwon(form.depositText)
  const rowErrors: Record<number, string> = {}
  for (const row of form.timetable) {
    const err = timetableRowError(row, buildings)
    if (err) rowErrors[row.id] = err
  }
  let departmentError: string | null = null
  if (form.departmentChoice === CUSTOM_DEPARTMENT && form.customDepartment.trim() === '') {
    departmentError = '학과 이름을 입력해 주세요.'
  } else if (buildings.length > 0 && form.departmentBuildings.some((b) => !buildings.includes(b))) {
    departmentError = '목록에 없는 학과 건물이 있습니다. 다시 골라 주세요.'
  }
  const budgetError = budget === undefined ? '0보다 큰 숫자를 입력해 주세요.' : null
  const depositError = deposit === undefined ? '0 이상의 숫자를 입력해 주세요.' : null
  return {
    budget,
    budgetError,
    deposit,
    depositError,
    departmentError,
    rowErrors,
    canSave:
      budgetError === null &&
      depositError === null &&
      departmentError === null &&
      Object.keys(rowErrors).length === 0,
  }
}

// 검증을 통과한 폼 → 저장값. savedAt 은 저장할 때 채운다.
export function toProfile(form: FormState, check: FormCheck, spotNames: string[]): Omit<Profile, 'savedAt'> {
  // 지금 대학의 강의동만 남긴다. 강의동 데이터가 없는 대학에서는 기존 방문 기록을 그대로 둔다.
  const visits =
    spotNames.length === 0
      ? form.visits
      : Object.fromEntries(Object.entries(form.visits).filter(([name]) => spotNames.includes(name)))
  const timetable: ClassSlot[] = form.timetable.map(({ day, start, end, building }) => ({ day, start, end, building }))
  const places: FrequentPlace[] = form.places.map((pl) => ({
    area: pl.area,
    kind: pl.kind,
    visitsPerWeek: pl.visitsPerWeek,
    fromCampus: pl.fromCampus,
    endsAt: pl.endsAt === '' ? null : pl.endsAt,
  }))
  return {
    department: departmentName(form),
    departmentBuildings: form.departmentBuildings,
    grade: form.grade,
    desiredCommuteMin: form.desiredCommuteMin,
    budgetManwon: check.budget ?? null,
    depositBudgetManwon: check.deposit ?? null,
    priorities: form.priorities,
    timetable,
    places,
    visits,
  }
}

// 변경 여부 비교용 (행 id 는 빼고 본다)
export function formSignature(form: FormState): string {
  return JSON.stringify({
    ...form,
    timetable: form.timetable.map(({ id: _id, ...rest }) => rest),
    places: form.places.map(({ id: _id, ...rest }) => rest),
  })
}
