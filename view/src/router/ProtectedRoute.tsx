import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Spin } from 'antd'
import { checkAuth } from '../services/authService'
import { buildLoginPath } from '../utils/authNavigation'

interface ProtectedRouteProps {
  children: React.ReactNode
}

function hasCachedActiveUser(token: string | null): boolean {
  if (!token) return false
  try {
    const cached = JSON.parse(localStorage.getItem('userInfo') || '{}') as {
      username?: string
      disabled?: boolean
    }
    return Boolean(cached.username) && cached.disabled !== true
  } catch {
    return false
  }
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const token = localStorage.getItem('token')
  const location = useLocation()
  const hasCachedUser = hasCachedActiveUser(token)

  const [checking, setChecking] = useState(!hasCachedUser)
  const [authed, setAuthed] = useState(hasCachedUser)

  useEffect(() => {
    let cancelled = false

    const run = async () => {
      if (!token) {
        if (!cancelled) {
          setAuthed(false)
          setChecking(false)
        }
        return
      }

      const ok = await checkAuth()
      if (!cancelled) {
        setAuthed(ok)
        setChecking(false)
      }
    }

    run()
    return () => {
      cancelled = true
    }
  }, [token])

  if (checking) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!token || !authed) {
    const from = `${location.pathname}${location.search}${location.hash}`
    return <Navigate to={buildLoginPath(from)} replace state={{ from }} />
  }

  return <>{children}</>
}
