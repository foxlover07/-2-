import { Sparkles } from 'lucide-react'
import { Badge, Card } from '../common'

// AI 추천은 아직 연동 전이다. 순위·점수를 만들어 보여 주지 않고 자리만 둔다.
export function AiRecommendPlaceholder() {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">AI 추천 TOP 3</h2>
        <Badge tone="gray">준비 중</Badge>
      </div>
      <p className="mt-1 text-sm text-muted">
        생활 프로필의 방문 횟수·예산·중요도를 반영한 추천이 이곳에 표시될 예정입니다.
      </p>
      <ol className="mt-4 grid gap-3 md:grid-cols-3">
        {[1, 2, 3].map((rank) => (
          <li
            key={rank}
            className="flex items-center gap-3 rounded-control border border-dashed border-border bg-bg px-4 py-4"
          >
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-white text-sm font-bold text-muted">
              {rank}
            </span>
            <span className="text-sm text-muted">추천 준비 중</span>
          </li>
        ))}
      </ol>
    </Card>
  )
}
