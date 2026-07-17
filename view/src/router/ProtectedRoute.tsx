import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Spin } from 'antd'
import { checkAuth } from '../services/authService'
import { buildLoginPath } from '../utils/authNavigation'

interface ProtectedRouteProps {
  children: React.ReactNode
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const token = localStorage.getItem('token')
  const location = useLocation()

  const [checking, setChecking] = useState(true)
  const [authed, setAuthed] = useState(false)

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
