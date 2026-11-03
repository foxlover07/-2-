import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { CompareProvider } from './contexts/CompareContext'
import { ProfileProvider } from './contexts/ProfileContext'
import { UniversityProvider } from './contexts/UniversityContext'
import { routes } from './routes'

const router = createBrowserRouter(routes)

function App() {
  return (
    <UniversityProvider>
      <ProfileProvider>
        <CompareProvider>
          <RouterProvider router={router} />
        </CompareProvider>
      </ProfileProvider>
    </UniversityProvider>
  )
}

export default App
