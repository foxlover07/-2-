import { useState } from 'react'
import { ChevronDown, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { Button, Chip, Input, Slider } from '../common'
import type { FilterBounds, NumberRange, PropertyFilters } from '../../utils/filter'
import { cx } from '../../utils/cx'

interface FilterPanelProps {
  filters: PropertyFilters
  bounds: FilterBounds
  onChange: (next: PropertyFilters) => void
  onReset: () => void
}

function parseNumber(raw: string): number | null {
  if (raw.trim() === '') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

interface RangeFieldProps {
  label: string
  unit: string
  range: NumberRange | null
  min: number | null
  max: number | null
  onChange: (min: number | null, max: number | null) => void
}

function RangeField({ label, unit, range, min, max, onChange }: RangeFieldProps) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-sm font-medium text-text">{label}</legend>
      <div className="flex items-center gap-2">
        <Input
          aria-label={`${label} 최소`}
          type="number"
          inputMode="decimal"
          min={0}
          placeholder={range ? `${range.min}` : '최소'}
          value={min ?? ''}
          onChange={(e) => onChange(parseNumber(e.target.value), max)}
          suffix={unit}
          className="flex-1"
        />
        <span className="text-muted">~</span>
        <Input
          aria-label={`${label} 최대`}
          type="number"
          inputMode="decimal"
          min={0}
          placeholder={range ? `${range.max}` : '최대'}
          value={max ?? ''}
          onChange={(e) => onChange(min, parseNumber(e.target.value))}
          suffix={unit}
          className="flex-1"
        />
      </div>
    </fieldset>
  )
}

export function FilterPanel({ filters, bounds, onChange, onReset }: FilterPanelProps) {
  // 모바일에서는 접어 두고, 데스크톱(lg 이상)에서는 항상 펼친다.
  const [open, setOpen] = useState(false)
  const set = (patch: Partial<PropertyFilters>) => onChange({ ...filters, ...patch })

  const costRange = bounds.cost && {
    min: Math.floor(bounds.cost.min),
    max: Math.ceil(bounds.cost.max),
  }

  return (
    <div className="p-4 lg:p-5">
      <div className="flex items-center justify-between">
        <button
          type="button"
          className="flex items-center gap-2 text-base font-bold text-navy lg:pointer-events-none"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <SlidersHorizontal className="size-4.5" aria-hidden />
          필터
          <ChevronDown className={cx('size-4 transition-transform lg:hidden', open && 'rotate-180')} aria-hidden />
        </button>
        <Button variant="ghost" size="sm" onClick={onReset}>
          <RotateCcw className="size-3.5" aria-hidden />
          초기화
        </Button>
      </div>

      <div className={cx('mt-5 flex-col gap-6', open ? 'flex' : 'hidden lg:flex')}>
        <RangeField
          label="월세"
          unit="만원"
          range={bounds.rent}
          min={filters.rentMin}
          max={filters.rentMax}
          onChange={(rentMin, rentMax) => set({ rentMin, rentMax })}
        />
        <RangeField
          label="관리비"
          unit="만원"
          range={bounds.maintenance}
          min={filters.maintenanceMin}
          max={filters.maintenanceMax}
          onChange={(maintenanceMin, maintenanceMax) => set({ maintenanceMin, maintenanceMax })}
        />
        <RangeField
          label="전용면적"
          unit="㎡"
          range={bounds.area}
          min={filters.areaMin}
          max={filters.areaMax}
          onChange={(areaMin, areaMax) => set({ areaMin, areaMax })}
        />

        <fieldset>
          <legend className="mb-2 text-sm font-medium text-text">주거 형태</legend>
          {bounds.types.length === 0 ? (
            <p className="text-sm text-muted">정보 없음</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Chip selected={filters.types.length === 0} onClick={() => set({ types: [] })}>
                전체
              </Chip>
              {bounds.types.map((type) => {
                const selected = filters.types.includes(type)
                return (
                  <Chip
                    key={type}
                    selected={selected}
                    onClick={() =>
                      set({ types: selected ? filters.types.filter((t) => t !== type) : [...filters.types, type] })
                    }
                  >
                    {type}
                  </Chip>
                )
              })}
            </div>
          )}
        </fieldset>

        {costRange && (
          <Slider
            label="월 고정비 상한"
            min={costRange.min}
            max={costRange.max}
            value={filters.costMax ?? costRange.max}
            onChange={(v) => set({ costMax: v >= costRange.max ? null : v })}
            formatValue={(v) => (filters.costMax === null ? '제한 없음' : `${v}만원 이하`)}
            minLabel={`${costRange.min}만원`}
            maxLabel={`${costRange.max}만원`}
          />
        )}
        <p className="-mt-3 text-xs text-muted">월 고정비 = 월세 + 관리비</p>
      </div>
    </div>
  )
}
