import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Check, Save } from 'lucide-react'
import { Button } from '../components/common'
import { PageContainer } from '../components/layout/PageContainer'
import { BudgetSection } from '../components/profile/BudgetSection'
import { DepartmentSection } from '../components/profile/DepartmentSection'
import { PlacesSection } from '../components/profile/PlacesSection'
import { PrioritySection } from '../components/profile/PrioritySection'
import { SimpleSettingsSection } from '../components/profile/SimpleSettingsSection'
import { TimetableSection } from '../components/profile/TimetableSection'
import { checkForm, formSignature, toForm, toProfile, type FormState } from '../components/profile/profileForm'
import { getUniversityLabel } from '../constants/universities'
import { useProfile } from '../contexts/ProfileContext'
import { useUniversity } from '../contexts/UniversityContext'
import { getProperties } from '../services/propertyService'
import type { Property } from '../types/property'
import { missingForRecommend } from '../utils/profile'
import { getSpotNames } from '../utils/walk'

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
  // 저장된 값 기준으로 추천에 빠진 입력
  const missing = isSaved ? missingForRecommend(profile) : []

  const patch = (p: Partial<FormState>) => setForm((f) => ({ ...f, ...p }))
  const setVisits = (name: string, count: number | null) =>
    setForm((f) => {
      const visits = { ...f.visits }
      if (count === null) delete visits[name]
      else visits[name] = count
      return { ...f, visits }
    })

  const handleSave = () => {
    if (!check.canSave) return
    const next = { ...toProfile(form, check, spotNames), savedAt: new Date().toISOString() }
    saveProfile(next)
    setForm(toForm(next))
    setJustSaved(true)
  }

  return (
    <PageContainer narrow>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-navy md:text-2xl">생활 프로필</h1>
          <p className="mt-1.5 text-sm text-muted">학과·예산·우선순위를 입력하면 조건 기반 추천에 반영됩니다.</p>
        </div>
        <p className="text-xs text-muted">마지막 저장: {formatSavedAt(profile.savedAt)}</p>
      </div>

      <div className="flex flex-col gap-5">
        <DepartmentSection
          step={1}
          departmentChoice={form.departmentChoice}
          customDepartment={form.customDepartment}
          departmentBuildings={form.departmentBuildings}
          grade={form.grade}
          desiredCommuteMin={form.desiredCommuteMin}
          buildings={spotNames}
          error={check.departmentError}
          onChange={patch}
        />

        <BudgetSection
          step={2}
          budgetText={form.budgetText}
          depositText={form.depositText}
          budget={check.budget}
          budgetError={check.budgetError}
          depositError={check.depositError}
          onChange={patch}
        />

        <PrioritySection step={3} priorities={form.priorities} onChange={(priorities) => patch({ priorities })} />

        <TimetableSection
          step={4}
          rows={form.timetable}
          onRowsChange={(timetable) => patch({ timetable })}
          rowErrors={check.rowErrors}
          buildings={spotNames}
        />

        <PlacesSection step={5} rows={form.places} onRowsChange={(places) => patch({ places })} />

        <SimpleSettingsSection
          step={6}
          loading={all === null}
          universityLabel={getUniversityLabel(university)}
          spotNames={spotNames}
          visits={form.visits}
          onVisitsChange={setVisits}
        />
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <p role="status" className="text-sm text-muted">
          {justSaved ? (
            <span className="flex items-center gap-1 font-medium text-green">
              <Check className="size-4" aria-hidden />
              저장되었습니다.{' '}
              {missing.length === 0 ? (
                <Link to="/compare" className="underline">
                  추천 보러 가기
                </Link>
              ) : (
                <span className="font-normal text-muted">추천에는 {missing.join(', ')}이(가) 필요합니다.</span>
              )}
            </span>
          ) : !check.canSave ? (
            '입력 오류를 고쳐야 저장할 수 있습니다.'
          ) : dirty ? (
            isSaved ? '저장하지 않은 변경 사항이 있습니다.' : '아직 저장된 프로필이 없습니다.'
          ) : missing.length > 0 ? (
            `추천을 받으려면 ${missing.join(', ')}을(를) 입력해 주세요.`          ) : (
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
