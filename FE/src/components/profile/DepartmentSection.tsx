import { CircleAlert, Footprints } from 'lucide-react'
import {
  COMMUTE_MAX,
  COMMUTE_MIN,
  COMMUTE_STEP,
  DEPARTMENT_BUILDINGS,
  DEPARTMENTS,
  GRADES,
} from '../../constants/recommend'
import { Badge, Chip, Select } from '../common'
import { SectionCard } from './SectionCard'
import { Stepper } from './Stepper'
import { CUSTOM_DEPARTMENT, TIME_INPUT } from './profileForm'

interface DepartmentSectionProps {
  step: number
  departmentChoice: string
  customDepartment: string
  departmentBuildings: string[]
  grade: number | null
  desiredCommuteMin: number
  buildings: string[] // 매물 데이터의 강의동 이름 (도보시간이 있는 건물만 고를 수 있다)
  error: string | null
  onChange: (patch: {
    departmentChoice?: string
    customDepartment?: string
    departmentBuildings?: string[]
    grade?: number | null
    desiredCommuteMin?: number
  }) => void
}

export function DepartmentSection({
  step,
  departmentChoice,
  customDepartment,
  departmentBuildings,
  grade,
  desiredCommuteMin,
  buildings,
  error,
  onChange,
}: DepartmentSectionProps) {
  const chooseDepartment = (choice: string) => {
    const preset = DEPARTMENT_BUILDINGS[choice]
    // 목록에 있는 학과를 고르면 학과 건물을 자동으로 채운다 (사용자가 고칠 수 있다)
    onChange({ departmentChoice: choice, ...(preset ? { departmentBuildings: [...preset] } : {}) })
  }
  const toggleBuilding = (name: string) =>
    onChange({
      departmentBuildings: departmentBuildings.includes(name)
        ? departmentBuildings.filter((b) => b !== name)
        : [...departmentBuildings, name],
    })
  const usesPreset = departmentChoice !== '' && departmentChoice !== CUSTOM_DEPARTMENT

  return (
    <SectionCard
      step={step}
      title="학과 · 희망 통학 시간"
      description="학과 건물까지의 도보시간이 이동시간 점수에 가장 크게 반영됩니다"
    >
      <div className="grid gap-4 sm:grid-cols-[1fr_1fr_8rem]">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          학과
          <Select value={departmentChoice} onChange={(e) => chooseDepartment(e.target.value)}>
            <option value="">학과 선택</option>
            {DEPARTMENTS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
            <option value={CUSTOM_DEPARTMENT}>목록에 없음 (직접 입력)</option>
          </Select>
        </label>
        {departmentChoice === CUSTOM_DEPARTMENT ? (
          <label className="flex flex-col gap-1.5 text-xs text-muted">
            학과 이름
            <input
              type="text"
              placeholder="예: 화학생명분자과학부"
              value={customDepartment}
              onChange={(e) => onChange({ customDepartment: e.target.value })}
              className={`${TIME_INPUT} ${customDepartment.trim() === '' ? 'border-red' : 'border-border'}`}
            />
          </label>
        ) : (
          <div className="hidden sm:block" />
        )}
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          학년
          <Select
            value={grade === null ? '' : String(grade)}
            onChange={(e) => onChange({ grade: e.target.value === '' ? null : Number(e.target.value) })}
          >
            <option value="">선택</option>
            {GRADES.map((g) => (
              <option key={g} value={g}>
                {g}학년
              </option>
            ))}
          </Select>
        </label>
      </div>

      <fieldset className="mt-5">
        <legend className="mb-2 flex flex-wrap items-center gap-2 text-sm font-medium text-text">
          학과 건물
          {usesPreset && <Badge tone="amber">예시값 · 팀 확인 필요</Badge>}
        </legend>
        {buildings.length === 0 ? (
          <p className="text-sm text-muted">강의동 정보를 불러오는 중이거나, 이 대학의 강의동 데이터가 없습니다.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {buildings.map((b) => (
              <Chip key={b} selected={departmentBuildings.includes(b)} onClick={() => toggleBuilding(b)}>
                {b}
              </Chip>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs text-muted">
          학과를 고르면 자동으로 채워지고, 직접 고칠 수 있습니다. 여러 건물을 쓰면 평균 도보시간을 씁니다. 지금은 도보시간
          데이터가 있는 강의동 {buildings.length}곳만 고를 수 있습니다.
        </p>
      </fieldset>

      {error && (
        <p role="alert" className="mt-3 flex items-center gap-1.5 text-xs text-red">
          <CircleAlert className="size-3.5" aria-hidden />
          {error}
        </p>
      )}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-control bg-bg px-4 py-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium text-text">
            <Footprints className="size-4 text-blue" aria-hidden />
            희망 통학 시간 (편도, 도보 기준)
          </p>
          <p className="text-xs text-muted">
            이 시간 안이면 완만하게, 넘으면 10분마다 점수가 절반으로 줄어듭니다. {COMMUTE_MIN}~{COMMUTE_MAX}분
          </p>
        </div>
        <Stepper
          label="희망 통학 시간"
          value={desiredCommuteMin}
          min={COMMUTE_MIN}
          max={COMMUTE_MAX}
          step={COMMUTE_STEP}
          unit="분"
          onChange={(desiredCommuteMin) => onChange({ desiredCommuteMin })}
        />
      </div>
    </SectionCard>
  )
}
