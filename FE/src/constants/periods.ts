// 대학별 교시 → 시각 (시간표 입력용). 시간표는 저장할 때 {요일, 시작, 종료, 건물} 로 바꾸므로 BE 는 교시를 모른다.
// 출처: 각 대학 학점교류 안내문·수강신청 유의사항 (2026-11-15 확인). TODO 표시는 팀 확인이 필요한 값.
import type { UniversityName } from './universities'

export interface Period {
  no: number // 교시 번호
  start: string // HH:MM
  end: string // HH:MM
}

const p = (no: number, start: string, end: string): Period => ({ no, start, end })

// 고려대·서강대가 같이 쓰는 75분 모듈 (09:00 시작, 15분 휴식)
const MODULE_75_FROM_9 = [
  p(1, '09:00', '10:15'),
  p(2, '10:30', '11:45'),
  p(3, '12:00', '13:15'),
  p(4, '13:30', '14:45'),
  p(5, '15:00', '16:15'),
  p(6, '16:30', '17:45'),
]

export const PERIODS: Record<UniversityName, Period[]> = {
  // 1~6교시 75분, 7교시부터 50분 (registrar.korea.ac.kr 2025-1 수강신청 유의사항)
  고려대학교: [...MODULE_75_FROM_9, p(7, '18:00', '18:50'), p(8, '19:00', '19:50'), p(9, '20:00', '20:50')],

  // 1·2교시만 학점교류 안내문으로 확인. 3교시부터는 고려대와 같은 75분 모듈로 가정 (TODO: 서강대 학생 확인)
  서강대학교: MODULE_75_FROM_9,

  // 75분, 08:00 시작 (2026-2 학점교류 안내. 대학원 문서라 학부 기준은 TODO: 이대 학생 확인)
  이화여자대학교: [
    p(1, '08:00', '09:15'),
    p(2, '09:30', '10:45'),
    p(3, '11:00', '12:15'),
    p(4, '12:30', '13:45'),
    p(5, '14:00', '15:15'),
    p(6, '15:30', '16:45'),
    p(7, '17:00', '18:15'),
    p(8, '18:30', '19:45'),
  ],

  // 50분, 매시 정각 (학점교류 안내의 주간·야간 표. 정규학기 기준은 TODO: 과기대 학생 확인)
  서울과학기술대학교: Array.from({ length: 14 }, (_, i) => {
    const h = String(9 + i).padStart(2, '0')
    return p(i + 1, `${h}:00`, `${h}:50`)
  }),
}

/** 고른 교시들을 연속 구간으로 묶는다. [1,2,4] → [[1,2],[4]] */
export function groupConsecutive(nos: number[]): number[][] {
  const sorted = [...new Set(nos)].sort((a, b) => a - b)
  const groups: number[][] = []
  for (const n of sorted) {
    const last = groups.at(-1)
    if (last && last.at(-1) === n - 1) last.push(n)
    else groups.push([n])
  }
  return groups
}

/** 시각 구간이 교시 구간과 정확히 맞으면 [시작 교시, 끝 교시], 아니면 null (예전에 시각으로 직접 입력한 수업) */
export function toPeriodRange(periods: Period[], start: string, end: string): [number, number] | null {
  const from = periods.find((x) => x.start === start)
  const to = periods.find((x) => x.end === end)
  return from && to && from.no <= to.no ? [from.no, to.no] : null
}

export function periodRangeLabel([from, to]: [number, number]): string {
  return from === to ? `${from}교시` : `${from}–${to}교시`
}
