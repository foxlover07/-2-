import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Building2, Check, GraduationCap, RotateCcw, Scale, Wallet } from 'lucide-react'
import { Button, Card, Chip, EmptyState, Input, Slider } from '../components/common'
import { PageContainer } from '../components/layout/PageContainer'
import { Stepper } from '../components/profile/Stepper'
import { getUniversityLabel } from '../constants/universities'
import { IMPORTANCE_MAX, IMPORTANCE_MIN, useProfile, type Profile } from '../contexts/ProfileContext'
import { useUniversity } from '../contexts/UniversityContext'
import { getProperties } from '../services/propertyService'
import type { Property } from '../types/property'
import { getFilterBounds } from '../utils/filter'
import { getSpotNames } from '../utils/walk'

// 하루 2번 × 7일
const VISITS_MIN = 1
const VISITS_MAX = 14
const IMPORTANCE_LABEL = ['', '매우 낮음', '낮음', '보통', '높음', '매우 높음']

interface FormState {
  visits: Profile['visits']
  budgetText: string
  costImportance: number
  walkImportance: number
}

function toForm(p: Profile): FormState {
  return {
    visits: p.visits,
    budgetText: p.budgetManwon === null ? '' : String(p.budgetManwon),
    costImportance: p.costImportance,
    walkImportance: p.walkImportance,
  }
}

// 빈 칸은 "예산 미설정"(null). 숫자가 아니거나 음수면 undefined(오류).
function parseBudget(text: string): number | null | undefined {
  if (text.trim() === '') return null
  const n = Number(text)
  return Number.isFinite(n) && n >= 0 ? n : undefined
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
  const spotNames = useMemo(() => getSpotNames(universityProperties), [universityProperties])
  const costRange = useMemo(() => getFilterBounds(universityProperties).cost, [universityProperties])

  const budget = parseBudget(form.budgetText)
  const budgetError = budget === undefined ? '0 이상의 숫자를 입력해 주세요.' : null
  const dirty = !isSaved || JSON.stringify(form) !== JSON.stringify(toForm(profile))
  const selectedCount = Object.keys(form.visits).length

  const setVisits = (name: string, count: number | null) =>
    setForm((f) => {
      const visits = { ...f.visits }
      if (count === null) delete visits[name]
      else visits[name] = count
      return { ...f, visits }
    })

  const handleSave = () => {
    if (budget === undefined) return
    // 지금 대학의 강의동만 남긴다. 다른 강의동이 섞이면 가중 평균을 계산할 수 없다.
    // 강의동 데이터가 없는 대학에서 저장할 때는 기존 방문 기록을 그대로 둔다.
    const visits =
      spotNames.length === 0
        ? form.visits
        : Object.fromEntries(Object.entries(form.visits).filter(([name]) => spotNames.includes(name)))
    const next: Profile = {
      visits,
      budgetManwon: budget,
      costImportance: form.costImportance,
      walkImportance: form.walkImportance,
    }
    saveProfile(next)
    setForm(toForm(next))
    setJustSaved(true)
  }

  return (
    <PageContainer
      title="생활 프로필"
      description="자주 가는 강의동과 예산을 알려 주시면 도보시간과 예산 비교에 반영합니다."
      narrow
    >
      <div className="flex flex-col gap-5">
        <Card>
          <div className="flex items-center gap-2">
            <GraduationCap className="size-5 text-blue" aria-hidden />
            <h2 className="text-base font-bold text-navy">자주 가는 강의동</h2>
          </div>
          <p className="mt-1 text-sm text-muted">
            {getUniversityLabel(university)} 강의동을 고르고 일주일에 몇 번 가는지 입력해 주세요.
          </p>

          {all === null ? (
            <p className="py-8 text-center text-sm text-muted">강의동 정보를 불러오는 중입니다…</p>
          ) : spotNames.length === 0 ? (
            <EmptyState
              icon={Building2}
              title={`${getUniversityLabel(university)} 강의동 정보가 아직 없습니다`}
              description="현재는 이화여대 강의동만 제공합니다."
            />
          ) : (
            <ul className="mt-4 divide-y divide-border">
              {spotNames.map((name) => {
                const count = form.visits[name]
                const selected = count !== undefined
                return (
                  <li key={name} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <Chip selected={selected} onClick={() => setVisits(name, selected ? null : VISITS_MIN)}>
                      {selected && <Check className="size-3.5" aria-hidden />}
                      {name}
                    </Chip>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted">주</span>
                      <Stepper
                        label={`${name} 주간 방문 횟수`}
                        value={count ?? 0}
                        min={VISITS_MIN}
                        max={VISITS_MAX}
                        unit="회"
                        disabled={!selected}
                        onChange={(v) => setVisits(name, v)}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
          {spotNames.length > 0 && selectedCount === 0 && (
            <p className="mt-2 text-xs text-muted">강의동을 하나 이상 선택해야 가중 평균 도보시간을 계산할 수 있습니다.</p>
          )}
        </Card>

        <Card>
          <div className="flex items-center gap-2">
            <Wallet className="size-5 text-blue" aria-hidden />
            <h2 className="text-base font-bold text-navy">월 예산 상한</h2>
          </div>
          <p className="mt-1 text-sm text-muted">월세와 관리비를 합한 월 고정 주거비 기준입니다.</p>
          <Input
            className="mt-4 max-w-xs"
            aria-label="월 예산 상한"
            type="number"
            inputMode="decimal"
            min={0}
            placeholder="예산 미설정"
            suffix="만원"
            value={form.budgetText}
            onChange={(e) => setForm((f) => ({ ...f, budgetText: e.target.value }))}
            aria-invalid={budgetError !== null}
            hint={
              budgetError ??
              (costRange ? `현재 매물의 월 고정비는 ${costRange.min}~${costRange.max}만원입니다. 비워 두면 예산을 비교하지 않습니다.` : '비워 두면 예산을 비교하지 않습니다.')
            }
          />
        </Card>

        <Card>
          <div className="flex items-center gap-2">
            <Scale className="size-5 text-blue" aria-hidden />
            <h2 className="text-base font-bold text-navy">중요도</h2>
          </div>
          <p className="mt-1 text-sm text-muted">AI 추천에서 주거비와 이동시간 중 무엇을 더 중요하게 볼지 정합니다.</p>
          <div className="mt-5 flex flex-col gap-6">
            <Slider
              label="주거비 중요도"
              min={IMPORTANCE_MIN}
              max={IMPORTANCE_MAX}
              value={form.costImportance}
              onChange={(v) => setForm((f) => ({ ...f, costImportance: v }))}
              formatValue={(v) => IMPORTANCE_LABEL[v]}
              minLabel="낮음"
              maxLabel="높음"
            />
            <Slider
              label="이동시간 중요도"
              min={IMPORTANCE_MIN}
              max={IMPORTANCE_MAX}
              value={form.walkImportance}
              onChange={(v) => setForm((f) => ({ ...f, walkImportance: v }))}
              formatValue={(v) => IMPORTANCE_LABEL[v]}
              minLabel="낮음"
              maxLabel="높음"
            />
          </div>
        </Card>
      </div>

      {/* 하단 고정 저장 바에 마지막 카드가 가리지 않도록 */}
      <div className="h-16" aria-hidden />

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3 md:px-6">
          <p role="status" className="text-sm text-muted">
            {justSaved ? (
              <span className="flex items-center gap-1 font-medium text-green">
                <Check className="size-4" aria-hidden />
                저장되었습니다.{' '}
                <Link to="/" className="underline">
                  매물 보러 가기
                </Link>
              </span>
            ) : dirty ? (
              isSaved ? '저장하지 않은 변경 사항이 있습니다.' : '아직 저장된 프로필이 없습니다.'
            ) : (
              '저장된 프로필입니다.'
            )}
          </p>
          <div className="flex shrink-0 gap-2">
            {isSaved && dirty && (
              <Button variant="ghost" onClick={() => setForm(toForm(profile))}>
                <RotateCcw className="size-4" aria-hidden />
                되돌리기
              </Button>
            )}
            <Button onClick={handleSave} disabled={budgetError !== null || !dirty}>
              {isSaved ? '수정 저장' : '저장'}
            </Button>
          </div>
        </div>
      </div>
    </PageContainer>
  )
}
