import type { RouteObject } from 'react-router'
import { AppLayout } from './components/layout/AppLayout'
import { ComparePage } from './pages/ComparePage'
import { ExplorePage } from './pages/ExplorePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { ProfilePage } from './pages/ProfilePage'
import { PropertyDetailPage } from './pages/PropertyDetailPage'

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <ExplorePage /> },
      { path: 'properties/:id', element: <PropertyDetailPage /> },
      { path: 'profile', element: <ProfilePage /> },
      { path: 'compare', element: <ComparePage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
