import { Link, NavLink } from 'react-router'
import { GraduationCap, House } from 'lucide-react'
import { isUniversityName, UNIVERSITIES } from '../../constants/universities'
import { useUniversity } from '../../contexts/UniversityContext'
import { cx } from '../../utils/cx'

const LINKS = [
  { to: '/', label: '매물 탐색', end: true },
  { to: '/profile', label: '생활 프로필', end: false },
  { to: '/compare', label: '매물 비교', end: false },
]

export function TopNav() {
  const { university, setUniversity } = useUniversity()

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-white">
      <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-4 px-4 md:h-16 md:gap-8 md:px-6">
        <Link to="/" aria-label="AI 주거 추천 홈" className="flex shrink-0 items-center gap-2 text-navy">
          <span className="flex size-8 items-center justify-center rounded-control bg-navy text-white">
            <House className="size-4.5" aria-hidden />
          </span>
          <span className="hidden text-base font-bold sm:inline">AI 주거 추천</span>
        </Link>

        <nav aria-label="주요 메뉴" className="-mx-1 flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                cx(
                  'shrink-0 rounded-control px-3 py-2 text-sm whitespace-nowrap transition-colors',
                  isActive ? 'bg-blue-50 font-bold text-blue' : 'font-medium text-muted hover:text-text',
                )
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        <label className="flex h-9 shrink-0 items-center gap-1.5 rounded-control border border-border bg-white pl-2.5 focus-within:border-blue">
          <GraduationCap className="size-4 text-blue" aria-hidden />
          <span className="sr-only">대학 선택</span>
          <select
            value={university}
            onChange={(e) => isUniversityName(e.target.value) && setUniversity(e.target.value)}
            className="h-full cursor-pointer rounded-control bg-transparent pr-2 text-sm font-medium text-text outline-none"
          >
            {UNIVERSITIES.map((u) => (
              <option key={u.name} value={u.name}>
                {u.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  )
}
