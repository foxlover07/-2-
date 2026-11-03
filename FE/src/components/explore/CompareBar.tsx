import { useNavigate } from 'react-router'
import { X } from 'lucide-react'
import { Button } from '../common'
import { MAX_COMPARE, useCompare } from '../../contexts/CompareContext'
import type { Property } from '../../types/property'
import { getMonthlyFixedCost } from '../../utils/cost'
import { formatManwon } from '../../utils/format'

interface CompareBarProps {
  // 비교 목록 ID를 매물 정보로 보여 주기 위한 전체 매물
  properties: Property[]
}

export function CompareBar({ properties }: CompareBarProps) {
  const { ids, remove, clear } = useCompare()
  const navigate = useNavigate()
  if (ids.length === 0) return null

  const canCompare = ids.length >= 2

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-white shadow-[0_-4px_16px_rgba(20,33,61,0.08)]">
      <div className="mx-auto flex max-w-[1440px] flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:px-6">
        <p className="shrink-0 text-sm font-bold text-navy">
          비교 목록 <span className="text-blue">{ids.length}</span>/{MAX_COMPARE}
        </p>

        <ul className="flex min-w-0 flex-1 gap-2 overflow-x-auto">
          {ids.map((id) => {
            const p = properties.find((v) => v.property_id === id)
            return (
              <li
                key={id}
                className="flex shrink-0 items-center gap-1.5 rounded-control border border-border bg-bg py-1 pr-1 pl-3 text-xs"
              >
                <span className="font-medium text-text">{id}</span>
                {p && (
                  <span className="text-muted">
                    {p.property_type} · {formatManwon(getMonthlyFixedCost(p))}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => remove(id)}
                  aria-label={`${id} 비교 목록에서 빼기`}
                  className="rounded-md p-1 text-muted hover:bg-white hover:text-text"
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              </li>
            )
          })}
        </ul>

        <div className="flex shrink-0 items-center gap-2">
          {!canCompare && <span className="text-xs text-muted">2개 이상 담으면 비교할 수 있어요</span>}
          <Button variant="ghost" size="sm" onClick={clear}>
            비우기
          </Button>
          <Button size="sm" disabled={!canCompare} onClick={() => navigate('/compare')}>
            비교하기
          </Button>
        </div>
      </div>
    </div>
  )
}
