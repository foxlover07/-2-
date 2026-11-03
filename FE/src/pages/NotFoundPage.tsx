import { Link } from 'react-router'
import { SearchX } from 'lucide-react'
import { EmptyState } from '../components/common'
import { PageContainer } from '../components/layout/PageContainer'

export function NotFoundPage() {
  return (
    <PageContainer>
      <EmptyState
        icon={SearchX}
        title="페이지를 찾을 수 없습니다"
        description="주소가 올바른지 확인해 주세요."
        action={
          <Link
            to="/"
            className="inline-flex h-10 items-center rounded-control bg-blue px-4 text-sm font-medium text-white hover:bg-blue/90"
          >
            매물 탐색으로 이동
          </Link>
        }
      />
    </PageContainer>
  )
}
