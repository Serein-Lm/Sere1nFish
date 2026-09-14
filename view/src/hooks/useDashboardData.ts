import { useCallback, useEffect, useRef, useState } from 'react'
import { getDashboardSnapshot, refreshDashboard } from '../services/dashboardService'

export function useDashboardData() {
  const [data, setData] = useState(getDashboardSnapshot)
  const [refreshing, setRefreshing] = useState(false)
  const mounted = useRef(false)
  const inFlight = useRef<Promise<boolean> | null>(null)
  const refresh = useCallback(() => {
    if (inFlight.current) return inFlight.current
    setRefreshing(true)
    const request = refreshDashboard(value => { if (mounted.current) setData(value) })
      .finally(() => {
        inFlight.current = null
        if (mounted.current) setRefreshing(false)
      })
    inFlight.current = request
    return request
  }, [])
  useEffect(() => {
    mounted.current = true
    void refresh()
    return () => { mounted.current = false }
  }, [refresh])
  return { ...data, loading: !data.overview && refreshing, refreshing, refresh }
}
