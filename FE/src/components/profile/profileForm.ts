// 생활 프로필 편집 폼 상태 ↔ 저장값(Profile) 변환과 검증. 화면 컴포넌트는 이 파일만 쓴다.
import { DEPARTMENTS, TIME_RE, type Area, type Day, type Factor, type LargerArea } from '../../constants/recommend'
import type { ClassSlot, FrequentPlace, Profile } from '../../utils/profile'

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
  visitsPerWeek: number
}

export interface FormState {
  departmentChoice: string // '' = 미선택, DEPARTMENTS 중 하나, 또는 CUSTOM_DEPARTMENT
  customDepartment: string
  departmentBuildings: string[]
  grade: number | null
  priorities: Factor[] // 1·2순위 (누른 순서)
  budgetText: string
  depositText: string
  commuteText: string
  areaText: string
  largerArea: LargerArea
  timetable: TimetableRow[]
  places: PlaceRow[]
}

const numText = (n: number | null) => (n === null ? '' : String(n))

export function toForm(p: Profile): FormState {
  const known = DEPARTMENTS.includes(p.department)
  return {
    departmentChoice: p.department === '' ? '' : known ? p.department : CUSTOM_DEPARTMENT,
    customDepartment: known ? '' : p.department,
    departmentBuildings: p.departmentBuildings,
    grade: p.grade,
    priorities: p.priorities,
    budgetText: numText(p.budgetManwon),
    depositText: numText(p.depositBudgetManwon),
    commuteText: numText(p.desiredCommuteMin),
    areaText: numText(p.desiredAreaM2),
    largerArea: p.largerArea,
    timetable: p.timetable.map((s) => ({ id: newRowId(), ...s })),
    places: p.places.map((pl) => ({ id: newRowId(), ...pl })),
  }
}

export function departmentName(form: FormState): string {
  return (form.departmentChoice === CUSTOM_DEPARTMENT ? form.customDepartment : form.departmentChoice).trim()
}

// 빈 칸은 미설정(null). 숫자가 아니거나 범위를 벗어나면 undefined(오류).
export function parseNumber(text: string, { positive = false } = {}): number | null | undefined {
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
  deposit: number | null | undefined
  commute: number | null | undefined
  area: number | null | undefined
  errors: { budget: string | null; deposit: string | null; commute: string | null; area: string | null }
  departmentError: string | null
  rowErrors: Record<number, string>
  canSave: boolean
}

const POSITIVE = '0보다 큰 숫자를 입력해 주세요.'

// deptBuildings: 학과 건물 선택지, ttBuildings: 시간표 건물 선택지 (도보시간 표가 있으면 더 많다)
export function checkForm(form: FormState, deptBuildings: string[], ttBuildings: string[] = deptBuildings): FormCheck {
  const budget = parseNumber(form.budgetText, { positive: true })
  const deposit = parseNumber(form.depositText)
  const commute = parseNumber(form.commuteText, { positive: true })
  const area = parseNumber(form.areaText, { positive: true })
  const rowErrors: Record<number, string> = {}
  for (const row of form.timetable) {
    const err = timetableRowError(row, ttBuildings)
    if (err) rowErrors[row.id] = err
  }
  let departmentError: string | null = null
  if (form.departmentChoice === CUSTOM_DEPARTMENT && form.customDepartment.trim() === '') {
    departmentError = '학과 이름을 입력해 주세요.'
  } else if (deptBuildings.length > 0 && form.departmentBuildings.some((b) => !deptBuildings.includes(b))) {
    departmentError = '목록에 없는 학과 건물이 있습니다. 다시 골라 주세요.'
  }
  const errors = {
    budget: budget === undefined ? POSITIVE : null,
    deposit: deposit === undefined ? '0 이상의 숫자를 입력해 주세요.' : null,
    commute: commute === undefined ? POSITIVE : null,
    area: area === undefined ? POSITIVE : null,
  }
  return {
    budget,
    deposit,
    commute,
    area,
    errors,
    departmentError,
    rowErrors,
    canSave: Object.values(errors).every((e) => e === null) && departmentError === null && Object.keys(rowErrors).length === 0,
  }
}

// 검증을 통과한 폼 → 저장값. savedAt 은 저장할 때 채운다.
export function toProfile(form: FormState, check: FormCheck): Omit<Profile, 'savedAt'> {
  const timetable: ClassSlot[] = form.timetable.map(({ day, start, end, building }) => ({ day, start, end, building }))
  const places: FrequentPlace[] = form.places.map(({ area, visitsPerWeek }) => ({ area, visitsPerWeek }))
  return {
    department: departmentName(form),
    departmentBuildings: form.departmentBuildings,
    grade: form.grade,
    priorities: form.priorities,
    budgetManwon: check.budget ?? null,
    depositBudgetManwon: check.deposit ?? null,
    desiredCommuteMin: check.commute ?? null,
    desiredAreaM2: check.area ?? null,
    largerArea: form.largerArea,
    timetable,
    places,
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
