import type { Day } from '../../constants/recommend'
import type { Period } from '../../constants/periods'

// 에브리타임 시간표처럼 요일 × 교시 칸에 건물별 색만 칠한 미리보기. 과목명은 받지 않으므로 건물 이름만 쓴다.
const WEEKDAYS: Day[] = ['월', '화', '수', '목', '금']

// 연한 배경 + 진한 글자 (건물이 추가된 순서대로 돌려 쓴다)
const PALETTE = [
  { bg: '#DCEBFF', fg: '#1D4ED8' },
  { bg: '#FDE2E4', fg: '#BE123C' },
  { bg: '#DCFCE7', fg: '#15803D' },
  { bg: '#FEF3C7', fg: '#B45309' },
  { bg: '#EDE9FE', fg: '#6D28D9' },
  { bg: '#CFFAFE', fg: '#0E7490' },
  { bg: '#FFE4D5', fg: '#C2410C' },
  { bg: '#E5E7EB', fg: '#374151' },
]

interface Slot {
  day: Day
  start: string
  end: string
  building: string
}

interface WeekGridProps {
  periods: Period[]
  slots: Slot[]
  // 수업이 없을 때도 보여 줄 최소 교시 수 (그보다 늦은 수업이 있으면 그 교시까지 늘린다)
  minRows?: number
}

export function WeekGrid({ periods, slots, minRows = 6 }: WeekGridProps) {
  // 수업 하나가 걸치는 교시 범위 (교시와 겹치는 칸 전부)
  const placed = slots
    .map((s) => {
      const idx = periods.flatMap((p, i) => (p.start < s.end && s.start < p.end ? [i] : []))
      return idx.length > 0 ? { ...s, from: idx[0], to: idx.at(-1)! } : null
    })
    .filter((x) => x !== null)

  const lastUsed = placed.reduce((m, s) => Math.max(m, s.to), -1)
  const rowCount = Math.min(periods.length, Math.max(minRows, lastUsed + 1))
  const shown = periods.slice(0, rowCount)

  const buildings = [...new Set(placed.map((s) => s.building))]
  const colorOf = (b: string) => PALETTE[buildings.indexOf(b) % PALETTE.length]

  return (
    <div
      className="grid overflow-hidden rounded-control border border-border bg-white text-xs"
      style={{ gridTemplateColumns: '3.25rem repeat(5, minmax(0, 1fr))', gridTemplateRows: `1.75rem repeat(${rowCount}, 2.5rem)` }}
      role="img"
      aria-label={`주간 시간표 미리보기: 수업 ${placed.length}개`}
    >
      {/* 머리글 */}
      <div className="border-b border-border bg-bg" style={{ gridColumn: 1, gridRow: 1 }} />
      {WEEKDAYS.map((d, i) => (
        <div
          key={d}
          className="flex items-center justify-center border-b border-l border-border bg-bg font-medium text-muted"
          style={{ gridColumn: i + 2, gridRow: 1 }}
        >
          {d}
        </div>
      ))}

      {/* 교시 칸 */}
      {shown.map((p, r) => (
        <div key={p.no} className="contents">
          <div
            className="flex flex-col items-center justify-center border-t border-border leading-tight text-muted"
            style={{ gridColumn: 1, gridRow: r + 2 }}
          >
            <span className="font-medium text-text">{p.no}</span>
            <span className="text-[10px]">{p.start}</span>
          </div>
          {WEEKDAYS.map((d, c) => (
            <div key={d} className="border-l border-t border-border" style={{ gridColumn: c + 2, gridRow: r + 2 }} />
          ))}
        </div>
      ))}

      {/* 수업 블록 */}
      {placed.map((s) => {
        const col = WEEKDAYS.indexOf(s.day)
        if (col < 0 || s.from >= rowCount) return null
        const { bg, fg } = colorOf(s.building)
        return (
          <div
            key={`${s.day}-${s.start}-${s.building}`}
            className="m-px overflow-hidden rounded-[6px] px-1.5 py-1 font-medium leading-tight"
            style={{
              gridColumn: col + 2,
              gridRow: `${s.from + 2} / ${Math.min(s.to, rowCount - 1) + 3}`,
              backgroundColor: bg,
              color: fg,
            }}
            title={`${s.day} ${s.start}–${s.end} ${s.building}`}
          >
            {s.building}
          </div>
        )
      })}
    </div>
  )
}
