import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Check, Save } from 'lucide-react'
import { Button } from '../components/common'
import { PageContainer } from '../components/layout/PageContainer'
import { PlacesSection } from '../components/profile/PlacesSection'
import { SectionCard } from '../components/profile/SectionCard'
import { SimpleSettingsSection } from '../components/profile/SimpleSettingsSection'
import { TimetableSection } from '../components/profile/TimetableSection'
import { WeightsSection } from '../components/profile/WeightsSection'
import { checkForm, formSignature, toForm, toProfile, type FormState } from '../components/profile/profileForm'
import { getUniversityLabel } from '../constants/universities'
import { useProfile } from '../contexts/ProfileContext'
import { useUniversity } from '../contexts/UniversityContext'
import { getProperties } from '../services/propertyService'
import type { Property } from '../types/property'
import { getSpotNames } from '../utils/walk'

// 예산 슬라이더 범위 (Figma 03 v2). 입력칸에는 이 범위 밖 값도 넣을 수 있다.
const BUDGET_SLIDER_MIN = 30
const BUDGET_SLIDER_MAX = 150

function formatSavedAt(iso: string | null): string {
  if (!iso) return '저장 기록 없음'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '저장 기록 없음'
  return d.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function ProfilePage() {
  const { university } = useUniversity()
  const { profile, isSaved, saveProfile } = useProfile()
  const [all, setAll] = useState<Property[] | null>(null)
  const [form, setForm] = useState<FormState>(() => toForm(profile))
  const [justSaved, setJustSaved] = useState(false)

  useEffect(() => {
    let cancelled = false
    getProperties()
      .then((data) => !cancelled && setAll(data))
      .catch(() => !cancelled && setAll([]))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!justSaved) return
    const timer = window.setTimeout(() => setJustSaved(false), 2500)
    return () => window.clearTimeout(timer)
  }, [justSaved])

  const universityProperties = useMemo(() => (all ?? []).filter((p) => p.university === university), [all, university])
  // 건물 선택지는 하드코딩하지 않고 매물 데이터의 강의동 이름에서 뽑는다.
  const spotNames = useMemo(() => getSpotNames(universityProperties), [universityProperties])

  const check = checkForm(form, spotNames)
  const savedForm = useMemo(() => toForm(profile), [profile])
  const dirty = !isSaved || formSignature(form) !== formSignature(savedForm)

  const patch = (p: Partial<FormState>) => setForm((f) => ({ ...f, ...p }))
  const setVisits = (name: string, count: number | null) =>
    setForm((f) => {
      const visits = { ...f.visits }
      if (count === null) delete visits[name]
      else visits[name] = count
      return { ...f, visits }
    })

  const handleSave = () => {
    if (!check.canSave || check.budget === undefined) return
    const next = { ...toProfile(form, check.budget, spotNames), savedAt: new Date().toISOString() }
    saveProfile(next)
    setForm(toForm(next))
    setJustSaved(true)
  }

  const sliderBudget = Math.min(BUDGET_SLIDER_MAX, Math.max(BUDGET_SLIDER_MIN, check.budget ?? BUDGET_SLIDER_MIN))

  return (
    <PageContainer narrow>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-navy md:text-2xl">생활 프로필</h1>
          <p className="mt-1.5 text-sm text-muted">시간표와 생활 패턴을 입력하면 조건 기반 추천에 반영됩니다.</p>
        </div>
        <p className="text-xs text-muted">마지막 저장: {formatSavedAt(profile.savedAt)}</p>
      </div>

      <div className="flex flex-col gap-5">
        <TimetableSection
          rows={form.timetable}
          onRowsChange={(timetable) => patch({ timetable })}
          rowErrors={check.rowErrors}
          buildings={spotNames}
          gapBufferMin={form.gapBufferMin}
          onGapBufferChange={(gapBufferMin) => patch({ gapBufferMin })}
        />

        <PlacesSection rows={form.places} onRowsChange={(places) => patch({ places })} />

        <SectionCard step={3} title="월 주거비 예산 상한" description="월 고정 주거비(월세 + 관리비) 기준">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <div className="flex h-12 w-44 items-center rounded-control border border-border bg-white px-3 focus-within:border-blue focus-within:ring-1 focus-within:ring-blue">
              <input
                aria-label="월 예산 상한"
                type="number"
                inputMode="decimal"
                min={0}
                placeholder="예산 미설정"
                value={form.budgetText}
                aria-invalid={check.budgetError !== null}
                onChange={(e) => patch({ budgetText: e.target.value })}
                className="w-full min-w-0 bg-transparent text-base font-bold text-text outline-none placeholder:text-sm placeholder:font-normal placeholder:text-muted"
              />
              <span className="ml-2 shrink-0 text-sm text-muted">만원 / 월</span>
            </div>
            <div className="min-w-48 flex-1">
              <input
                type="range"
                aria-label="월 예산 상한 슬라이더"
                min={BUDGET_SLIDER_MIN}
                max={BUDGET_SLIDER_MAX}
                value={sliderBudget}
                onChange={(e) => patch({ budgetText: e.target.value })}
                className="h-2 w-full cursor-pointer accent-blue"
              />
              <div className="mt-1 flex justify-between text-xs text-muted">
                <span>{BUDGET_SLIDER_MIN}만원</span>
                <span>{BUDGET_SLIDER_MAX}만원</span>
              </div>
            </div>
          </div>
          <p className={check.budgetError ? 'mt-3 text-xs text-red' : 'mt-3 text-xs text-muted'}>
            {check.budgetError ?? "넘는 매물은 추천 결과에 '예산 초과' 배지로 표시됩니다. 비워 두면 예산을 보지 않습니다."}
          </p>
        </SectionCard>

        <WeightsSection
          weights={form.weightsPercent}
          sum={check.weightsSum}
          onChange={(weightsPercent) => patch({ weightsPercent })}
        />

        <SimpleSettingsSection
          loading={all === null}
          universityLabel={getUniversityLabel(university)}
          spotNames={spotNames}
          visits={form.visits}
          onVisitsChange={setVisits}
          costImportance={form.costImportance}
          walkImportance={form.walkImportance}
          onCostImportanceChange={(costImportance) => patch({ costImportance })}
          onWalkImportanceChange={(walkImportance) => patch({ walkImportance })}
        />
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <p role="status" className="text-sm text-muted">
          {justSaved ? (
            <span className="flex items-center gap-1 font-medium text-green">
              <Check className="size-4" aria-hidden />
              저장되었습니다.{' '}
              <Link to="/compare" className="underline">
                추천 보러 가기
              </Link>
            </span>
          ) : !check.canSave ? (
            '입력 오류를 고쳐야 저장할 수 있습니다.'
          ) : dirty ? (
            isSaved ? '저장하지 않은 변경 사항이 있습니다.' : '아직 저장된 프로필이 없습니다.'
          ) : (
            '저장된 프로필입니다.'
          )}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => setForm(savedForm)} disabled={!dirty || !isSaved}>
            취소
          </Button>
          <Button onClick={handleSave} disabled={!check.canSave || !dirty}>
            <Save className="size-4" aria-hidden />
            프로필 저장
          </Button>
        </div>
      </div>
    </PageContainer>
  )
}
