import { useRef, useState } from 'react'
import { CircleAlert, ImageUp } from 'lucide-react'
import { API_URL, ApiError } from '../../services/api'
import { readTimetableImage } from '../../services/agentService'
import type { TimetableImageResponse } from '../../types/agent'
import { Badge, Button, Loading, Select } from '../common'
import { newRowId, type TimetableRow } from './profileForm'

// 시간표 캡처 → AI(Gemini)가 수업 칸을 읽고 → 여기서 확인·수정 → 시간표 표에 추가.
// 읽은 결과는 바로 저장하지 않는다 (CLAUDE.md: 시간표 VLM 은 입력 결과 확인 UI 포함). 이미지는 서버에 저장하지 않는다.

interface Draft {
  key: number
  include: boolean
  day: TimetableRow['day']
  start: string
  end: string
  building: string // '' = 아직 안 고름
  buildingText: string
  course: string | null
  issue: string | null
}

export function TimetableImageImport({ university, onAdd }: { university: string; onAdd: (rows: TimetableRow[]) => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<TimetableImageResponse | null>(null)
  const [drafts, setDrafts] = useState<Draft[]>([])

  const pick = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await readTimetableImage(file, university)
      setResult(res)
      setDrafts(
        res.slots.map((s, i) => ({
          key: i,
          include: !s.issue || s.building === null, // 시간 오류만 기본 제외, 건물은 여기서 고르면 된다
          day: s.day,
          start: s.start,
          end: s.end,
          building: s.building ?? '',
          buildingText: s.building_text,
          course: s.course,
          issue: s.issue,
        })),
      )
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : '사진을 읽지 못했습니다.')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const patch = (key: number, p: Partial<Draft>) => setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...p } : d)))
  const chosen = drafts.filter((d) => d.include)
  const add = () => {
    onAdd(chosen.map((d) => ({ id: newRowId(), day: d.day, start: d.start, end: d.end, building: d.building })))
    setResult(null)
    setDrafts([])
  }

  const disabled = API_URL === null || busy
  return (
    <div className="rounded-control border border-dashed border-border bg-bg/50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-control border border-border bg-white text-muted">
          <ImageUp className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-text">시간표 캡처로 불러오기</p>
          <p className="text-xs text-muted">
            에브리타임 등 시간표 캡처를 올리면 AI가 수업 칸을 읽어요. 확인한 뒤 아래 표에 넣습니다. (PNG·JPG·WEBP, 5MB 이하)
          </p>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          id="timetable-image"
          onChange={(e) => void pick(e.target.files?.[0])}
          disabled={disabled}
        />
        <Button variant="secondary" size="sm" disabled={disabled} onClick={() => fileRef.current?.click()}>
          사진 올리기
        </Button>
      </div>

      {API_URL === null && <p className="mt-2 text-xs text-amber">서버 주소(VITE_API_URL)가 없어 사진 입력을 쓸 수 없습니다.</p>}
      {busy && <Loading message="사진에서 수업 칸을 읽는 중…" className="mt-3 text-left" />}
      {error && (
        <p role="alert" className="mt-3 flex items-center gap-1.5 text-xs text-red">
          <CircleAlert className="size-3.5" aria-hidden />
          {error}
        </p>
      )}

      {result && (
        <div className="mt-3 rounded-control border border-border bg-white p-3">
          <p className={result.status === 'ok' ? 'text-sm text-text' : 'text-sm text-red'}>{result.message}</p>
          {result.notes.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs text-muted">
              {result.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {drafts.length > 0 && (
            <>
              <ul className="mt-3 flex flex-col gap-2">
                {drafts.map((d) => (
                  <li key={d.key} className="flex flex-wrap items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={d.include}
                      onChange={(e) => patch(d.key, { include: e.target.checked })}
                      aria-label={`${d.day} ${d.start} 수업 넣기`}
                    />
                    <span className="w-28 shrink-0 tabular-nums">
                      {d.day} {d.start}~{d.end}
                    </span>
                    <Select
                      aria-label={`${d.day} ${d.start} 강의 건물`}
                      value={d.building}
                      onChange={(e) => patch(d.key, { building: e.target.value })}
                      className="min-w-0 flex-1"
                    >
                      <option value="">건물 선택 (읽은 표기: {d.buildingText || '없음'})</option>
                      {result.buildings.map((b) => (
                        <option key={b} value={b}>
                          {b}
                        </option>
                      ))}
                    </Select>
                    {d.course && <span className="text-xs text-muted">{d.course}</span>}
                    {d.issue && <Badge tone="amber">{d.issue}</Badge>}
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex justify-end gap-2">
                <Button variant="secondary" size="sm" onClick={() => setResult(null)}>
                  취소
                </Button>
                <Button size="sm" disabled={chosen.length === 0} onClick={add}>
                  선택한 {chosen.length}칸 시간표에 추가
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
