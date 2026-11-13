import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Footprints, Trophy, Wallet, X } from 'lucide-react'
import { Badge, PlaceholderImage } from '../common'
import type { Property } from '../../types/property'
import { getMonthlyFixedCost, isWithinBudget } from '../../utils/cost'
import { formatArea, formatManwon, formatMinutes } from '../../utils/format'
import { getSpotNames, getWalkMin, getWeightedAvgWalk, type SpotVisits } from '../../utils/walk'
import { cx } from '../../utils/cx'

interface CompareTableProps {
  properties: Property[]
  cheapestId: string | null
  shortestId: string | null
  visits: SpotVisits
  budget: number | null
  onRemove: (id: string) => void
}

function SectionRow({ icon, title, colSpan }: { icon: ReactNode; title: string; colSpan: number }) {
  return (
    <tr>
      <th colSpan={colSpan} scope="colgroup" className="bg-bg px-4 py-2 text-left text-xs font-bold text-navy">
        <span className="flex items-center gap-1.5">
          {icon}
          {title}
        </span>
      </th>
    </tr>
  )
}

interface RowProps {
  label: ReactNode
  cells: Array<{ key: string; content: ReactNode; highlight?: 'green' | 'blue' | 'soft' }>
  strong?: boolean
}

const HIGHLIGHT = {
  green: 'bg-green-50 text-green',
  blue: 'bg-blue-50 text-blue',
  soft: 'text-blue',
}

function Row({ label, cells, strong = false }: RowProps) {
  return (
    <tr className="border-t border-border">
      <th scope="row" className="px-4 py-3 text-left text-sm font-medium text-muted">
        {label}
      </th>
      {cells.map((c) => (
        <td
          key={c.key}
          className={cx(
            'px-4 py-3 text-sm',
            strong ? 'text-base font-bold' : 'font-medium',
            c.highlight ? HIGHLIGHT[c.highlight] : 'text-text',
          )}
        >
          {c.content}
        </td>
      ))}
    </tr>
  )
}

// 행 안에서 가장 작은 값을 가진 칸(여러 개일 수 있음). 값이 하나뿐이면 강조하지 않는다.
function minKeys(values: Array<{ key: string; value: number | null }>): Set<string> {
  const nums = values.filter((v): v is { key: string; value: number } => v.value !== null)
  if (nums.length < 2) return new Set()
  const min = Math.min(...nums.map((v) => v.value))
  return new Set(nums.filter((v) => v.value === min).map((v) => v.key))
}

export function CompareTable({ properties, cheapestId, shortestId, visits, budget, onRemove }: CompareTableProps) {
  const colSpan = properties.length + 1
  const spotNames = getSpotNames(properties)
  const hasVisits = Object.values(visits).some((n) => n > 0)
  const cells = (render: (p: Property) => ReactNode, highlight?: (p: Property) => RowProps['cells'][number]['highlight']) =>
    properties.map((p) => ({ key: p.property_id, content: render(p), highlight: highlight?.(p) }))

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[600px] table-fixed border-collapse">
        <colgroup>
          <col className="w-36" />
          {properties.map((p) => (
            <col key={p.property_id} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <td />
            {properties.map((p) => (
              <th key={p.property_id} scope="col" className="px-4 pt-4 pb-3 text-left align-top font-normal">
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => onRemove(p.property_id)}
                    aria-label={`${p.property_id} 비교에서 빼기`}
                    className="absolute top-1.5 right-1.5 z-10 rounded-full bg-white/90 p-1 text-muted shadow-sm hover:text-text"
                  >
                    <X className="size-3.5" aria-hidden />
                  </button>
                  <PlaceholderImage className="h-24 w-full rounded-control" />
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {p.property_id === cheapestId && (
                    <Badge tone="green">
                      <Wallet className="size-3" aria-hidden />
                      최저 비용
                    </Badge>
                  )}
                  {p.property_id === shortestId && (
                    <Badge tone="blue">
                      <Trophy className="size-3" aria-hidden />
                      최단 이동
                    </Badge>
                  )}
                </div>
                <p className="mt-1.5 text-xs text-muted">
                  {p.property_id} · {p.property_type} · {formatArea(p.area_m2)}
                </p>
                <p className="mt-0.5 text-sm font-bold break-keep text-text">{p.address}</p>
                <Link to={`/properties/${p.property_id}`} className="mt-1 inline-block text-xs font-medium text-blue hover:underline">
                  상세 보기
                </Link>
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          <SectionRow icon={<Wallet className="size-3.5" aria-hidden />} title="비용" colSpan={colSpan} />
          <Row label="보증금" cells={cells((p) => formatManwon(p.deposit_manwon))} />
          <Row label="월세" cells={cells((p) => formatManwon(p.monthly_rent_manwon))} />
          <Row label="관리비" cells={cells((p) => formatManwon(p.maintenance_manwon))} />
          <Row
            label="월 고정 주거비"
            strong
            cells={cells(
              (p) => formatManwon(getMonthlyFixedCost(p)),
              (p) => (p.property_id === cheapestId ? 'green' : undefined),
            )}
          />
          {budget !== null && (
            <Row
              label={`예산 ${formatManwon(budget)}`}
              cells={cells((p) => {
                const within = isWithinBudget(p, budget)
                if (within === null) return <span className="text-muted">비교 불가</span>
                return within ? <Badge tone="green">예산 이내</Badge> : <Badge tone="gray">예산 초과</Badge>
              })}
            />
          )}

          <SectionRow icon={<Footprints className="size-3.5" aria-hidden />} title="강의동별 도보시간" colSpan={colSpan} />
          {spotNames.map((name) => {
            const fastest = minKeys(properties.map((p) => ({ key: p.property_id, value: getWalkMin(p, name) })))
            const count = visits[name] ?? 0
            return (
              <Row
                key={name}
                label={
                  <span className="flex flex-wrap items-center gap-1">
                    {name}
                    {count > 0 && <Badge tone="blue">학과 건물</Badge>}
                  </span>
                }
                cells={cells(
                  (p) => formatMinutes(getWalkMin(p, name)),
                  (p) => (fastest.has(p.property_id) ? 'soft' : undefined),
                )}
              />
            )
          })}
          <Row
            label="학과 건물 평균 도보시간"
            strong
            cells={cells(
              (p) => formatMinutes(getWeightedAvgWalk(p, visits)),
              (p) => (p.property_id === shortestId ? 'blue' : undefined),
            )}
          />
          {!hasVisits && (
            <tr>
              <td colSpan={colSpan} className="px-4 pb-3 text-xs text-muted">
                <Link to="/profile" className="font-medium text-blue underline">
                  생활 프로필
                </Link>
                에서 학과 건물을 입력하면 학과 건물 평균 도보시간과 최단 이동 매물을 알려 드려요.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
