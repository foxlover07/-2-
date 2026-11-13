import { UNIVERSITIES, type UniversityName } from '../../constants/universities'
import { Chip } from '../common'
import { SectionCard } from './SectionCard'

interface UniversitySectionProps {
  step: number
  university: UniversityName
  onChange: (name: UniversityName) => void
}

// 대학 선택 (통합안 1.1). 상단 메뉴의 대학 선택과 같은 값이다. 그 대학 매물만 추천한다.
export function UniversitySection({ step, university, onChange }: UniversitySectionProps) {
  return (
    <SectionCard step={step} title="대학 선택" description="고른 대학의 매물만 추천합니다 · 상단 메뉴의 대학 선택과 같은 값입니다">
      <div className="flex flex-wrap gap-2" role="group" aria-label="대학">
        {UNIVERSITIES.map((u) => (
          <Chip key={u.name} selected={university === u.name} onClick={() => onChange(u.name)}>
            {u.label}
          </Chip>
        ))}
      </div>
    </SectionCard>
  )
}
