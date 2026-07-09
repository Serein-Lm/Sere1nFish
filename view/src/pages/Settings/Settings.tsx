import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Settings() {
  const navigate = useNavigate()

  useEffect(() => {
    // 默认重定向到用户管理
    navigate('/settings/users', { replace: true })
  }, [navigate])

  return null
}
