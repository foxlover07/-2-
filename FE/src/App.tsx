import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { CompareProvider } from './contexts/CompareContext'
import { FavoriteProvider } from './contexts/FavoriteContext'
import { ProfileProvider } from './contexts/ProfileContext'
import { UniversityProvider } from './contexts/UniversityContext'
import { routes } from './routes'

const router = createBrowserRouter(routes)

function App() {
  return (
    <UniversityProvider>
      <ProfileProvider>
        <CompareProvider>
          <FavoriteProvider>
            <RouterProvider router={router} />
          </FavoriteProvider>
        </CompareProvider>
      </ProfileProvider>
    </UniversityProvider>
  )
}

export default App
