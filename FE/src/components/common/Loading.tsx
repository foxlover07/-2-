import { useEffect, useState } from 'react'
import { API_URL } from '../../services/api'
import { cx } from '../../utils/cx'

// 무료 플랜 서버(Render)는 한동안 요청이 없으면 잠들어, 첫 요청이 30초~1분 걸릴 수 있다.
// 5초가 지나도 끝나지 않으면 그 사실을 알려 고장으로 보이지 않게 한다.
const SLOW_AFTER_MS = 5000

export function Loading({ message, className }: { message: string; className?: string }) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    if (API_URL === null) return
    const t = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS)
    return () => window.clearTimeout(t)
  }, [])
  return (
    <div role="status" className={cx('text-center text-sm text-muted', className)}>
      <p>{message}</p>
      {slow && <p className="mt-1 text-xs">서버가 잠에서 깨는 중이라 처음 한 번은 1분 정도 걸릴 수 있어요.</p>}
    </div>
  )
}
