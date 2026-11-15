import { Outlet } from 'react-router'
import { ChatPanel } from '../chat/ChatPanel'
import { TopNav } from './TopNav'

export function AppLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <TopNav />
      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
      {/* AI 주거 상담: 화면 이동 중에도 대화가 유지되도록 레이아웃에 둔다 */}
      <ChatPanel />
    </div>
  )
}
