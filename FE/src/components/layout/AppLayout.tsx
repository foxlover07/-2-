import { Outlet } from 'react-router'
import { TopNav } from './TopNav'

export function AppLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <TopNav />
      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}
