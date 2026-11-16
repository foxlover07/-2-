import { CircleAlert, X } from 'lucide-react'
import { Chip } from '../common'
import { SectionCard } from './SectionCard'

interface DepartmentSectionProps {
  step: number
  departmentBuildings: string[]
  // 학교 건물 전체 (BE GET /recommend/buildings: 도보시간 표 + 건물 기준표). 도보시간이 있는 건물이 먼저
  buildings: string[]
  // 도보시간이 일부·전혀 없는 건물 → 안내 (예: '매물 6/161곳 · 고르면 계산')
  buildingNotes: Record<string, string>
  error: string | null
  onChange: (patch: { departmentBuildings: string[] }) => void
}

// 수업 듣는 건물 (2026-11-16 사용자 결정: 학과·학과 이름·학년 칸을 없애고 학교 건물 전체에서 고른다).
// 도보시간이 없는 건물을 고르면 추천할 때 빈 매물만 카카오 도보 경로로 계산한다 (BE app/building_walks).
export function DepartmentSection({
  step,
  departmentBuildings,
  buildings,
  buildingNotes,
  error,
  onChange,
}: DepartmentSectionProps) {
  const toggle = (name: string) =>
    onChange({
      departmentBuildings: departmentBuildings.includes(name)
        ? departmentBuildings.filter((b) => b !== name)
        : [...departmentBuildings, name],
    })
  // 2026-11-16 사용자 결정: '도보시간 준비됨 / 고르면 계산' 두 묶음을 나누지 않고 한 목록으로 보여 준다.
  // 아직 도보시간이 없는 건물을 골랐을 때만 계산 안내를 띄운다.
  const pickedLater = departmentBuildings.some((b) => buildingNotes[b])
  // 저장돼 있지만 지금 목록에 없는 건물 (예전 이름·다른 대학). 보여 줘야 뺄 수 있다
  const unknown = buildings.length > 0 ? departmentBuildings.filter((b) => !buildings.includes(b)) : []

  return (
    <SectionCard
      step={step}
      title="학과 건물"
    >
      {buildings.length === 0 ? (
        <p className="text-sm text-muted">건물 목록을 불러오는 중이거나, 이 대학의 건물 데이터가 없습니다.</p>
      ) : (
        <fieldset>
          <legend className="sr-only">건물 선택</legend>
          {unknown.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {unknown.map((b) => (
                <button
                  key={b}
                  type="button"
                  onClick={() => toggle(b)}
                  className="inline-flex items-center gap-1 rounded-full border border-red bg-red-50 px-3 py-1.5 text-sm text-red hover:bg-red-50/70"
                  title="지금 목록에 없는 건물 · 눌러서 빼기"
                >
                  {b} <X className="size-3.5" aria-hidden />
                  <span className="sr-only">빼기</span>
                </button>
              ))}
            </div>
          )}
          <div className="flex flex-wrap gap-1.5">
            {buildings.map((b) => (
              <Chip key={b} selected={departmentBuildings.includes(b)} onClick={() => toggle(b)} title={buildingNotes[b]}>
                {b}
              </Chip>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">
            학과 건물은 소속 단과대 건물이에요. 보통 1개를 고르고, 복수전공·부전공처럼 주로 다니는 단과대가 여럿이면 그만큼
            고르세요 (여러 개면 평균으로 계산해요).
          </p>
          {pickedLater && (
            <p className="mt-2 text-xs text-muted">
              고른 건물 중 처음 계산하는 건물은 추천할 때 카카오 도보 경로로 계산해 수십 초 걸릴 수 있습니다. 한 번 계산하면
              저장해 두고 다시 씁니다.
            </p>
          )}
        </fieldset>
      )}

      {error && (
        <p role="alert" className="mt-3 flex items-center gap-1.5 text-xs text-red">
          <CircleAlert className="size-3.5" aria-hidden />
          {error}
        </p>
      )}
    </SectionCard>
  )
}
