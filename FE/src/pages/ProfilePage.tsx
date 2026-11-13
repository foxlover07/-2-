import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Check, Save } from 'lucide-react'
import { Button } from '../components/common'
import { PageContainer } from '../components/layout/PageContainer'
import { ConditionsSection } from '../components/profile/ConditionsSection'
import { DepartmentSection } from '../components/profile/DepartmentSection'
import { PlacesSection } from '../components/profile/PlacesSection'
import { PrioritySection } from '../components/profile/PrioritySection'
import { TimetableSection } from '../components/profile/TimetableSection'
import { UniversitySection } from '../components/profile/UniversitySection'
import { checkForm, formSignature, toForm, toProfile, type FormState } from '../components/profile/profileForm'
import { DEFAULT_UNIVERSITY } from '../constants/universities'
import { useProfile } from '../contexts/ProfileContext'
import { useUniversity } from '../contexts/UniversityContext'
import { API_URL } from '../services/api'
import { getProperties } from '../services/propertyService'
import { getBuildings, type BuildingCoverage } from '../services/recommendService'
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
  const { university, setUniversity } = useUniversity()
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

  // 시간표 건물: BE 도보시간 표가 있으면 그 건물 목록(일부 매물만 계산된 건물 포함), 없으면 매물 데이터의 강의동
  // 응답을 대학과 함께 저장해, 대학을 바꾸면 새 응답이 올 때까지 이전 대학 건물을 쓰지 않는다
  const [buildingsResp, setBuildingsResp] = useState<{ university: string; list: BuildingCoverage[] } | null>(null)
  const apiBuildings = buildingsResp?.university === university ? buildingsResp.list : null
  useEffect(() => {
    if (API_URL === null) return
    const ctrl = new AbortController()
    getBuildings(university, ctrl.signal)
      .then((list) => setBuildingsResp({ university, list }))
      .catch(() => {})
    return () => ctrl.abort()
  }, [university])
  // 매물의 1/4 이상에 도보시간이 있는 건물만 고르게 한다. 그보다 적은 건물(예전 표본에서만 계산된 건물)을 고르면
  // 대부분의 매물이 추천에서 빠지기 때문이다. 계산이 늘면 자동으로 목록에 들어온다.
  const MIN_COVERAGE = 0.25
  const ttBuildings = useMemo(
    () => apiBuildings?.filter((b) => b.covered / b.total >= MIN_COVERAGE).map((b) => b.name) ?? spotNames,
    [apiBuildings, spotNames],
  )
  const ttNotes = useMemo(
    () =>
      Object.fromEntries(
        (apiBuildings ?? []).filter((b) => b.covered < b.total).map((b) => [b.name, `매물 ${b.covered}/${b.total}곳`]),
      ),
    [apiBuildings],
  )

  const check = checkForm(form, spotNames, ttBuildings)
  const savedForm = useMemo(() => toForm(profile), [profile])
  const dirty = !isSaved || formSignature(form) !== formSignature(savedForm)
  // 저장된 값 기준으로 추천에 빠진 입력
  const missing = isSaved ? missingForRecommend(profile) : []

  const patch = (p: Partial<FormState>) => setForm((f) => ({ ...f, ...p }))

  const handleSave = () => {
    if (!check.canSave) return
    const next = { ...toProfile(form, check), savedAt: new Date().toISOString() }
    saveProfile(next)
    setForm(toForm(next))
    setJustSaved(true)
  }

  return (
    <PageContainer narrow>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-navy md:text-2xl">생활 프로필</h1>
          <p className="mt-1.5 text-sm text-muted">
            조건으로 매물을 거른 뒤, 예산 · 이동 시간 · 면적을 우선순위대로 반영해 순위를 매깁니다.
          </p>
        </div>
        <p className="text-xs text-muted">마지막 저장: {formatSavedAt(profile.savedAt)}</p>
      </div>

      <div className="flex flex-col gap-5">
        <UniversitySection step={1} university={university} onChange={setUniversity} />

        <DepartmentSection
          step={2}
          departmentChoice={form.departmentChoice}
          customDepartment={form.customDepartment}
          departmentBuildings={form.departmentBuildings}
          grade={form.grade}
          buildings={spotNames}
          hasPresets={university === DEFAULT_UNIVERSITY}
          error={check.departmentError}
          onChange={patch}
        />

        <PrioritySection step={3} selected={form.priorities} onChange={(priorities) => patch({ priorities })} />

        <ConditionsSection
          step={4}
          budgetText={form.budgetText}
          depositText={form.depositText}
          commuteText={form.commuteText}
          areaText={form.areaText}
          largerArea={form.largerArea}
          budget={check.budget}
          errors={check.errors}
          onChange={patch}
        />

        <TimetableSection
          step={5}
          rows={form.timetable}
          onRowsChange={(timetable) => patch({ timetable })}
          rowErrors={check.rowErrors}
          buildings={ttBuildings}
          buildingNotes={ttNotes}
        />

        <PlacesSection step={6} rows={form.places} onRowsChange={(places) => patch({ places })} />
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
