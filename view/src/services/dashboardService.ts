import { getToken } from './http'
import { getOverview, getScenarios, getTurns, queryLogs, type OverviewData, type TokenTurn, type ScenarioStat, type LogEntry } from './observabilityService'
import { listProjects, type Project } from './projectService'

export interface DashboardSnapshot {
  overview: OverviewData | null
  turns: TokenTurn[]
  scenarios: ScenarioStat[]
  logs: LogEntry[]
  projects: Project[]
  projectTotal: number
  lastUpdatedAt: number | null
  errors: Partial<Record<ResourceKey, string>>
}
type ResourceKey = 'overview' | 'turns' | 'scenarios' | 'logs' | 'projects'
const empty = (): DashboardSnapshot => ({ overview: null, turns: [], scenarios: [], logs: [], projects: [], projectTotal: 0, lastUpdatedAt: null, errors: {} })
let owner: string | null = null
let snapshot = empty()
let fetched: Partial<Record<ResourceKey, number>> = {}
let pending = new Map<ResourceKey, Promise<void>>()

export function getDashboardSnapshot(): DashboardSnapshot {
  const token = getToken()
  if (token !== owner) {
    owner = token
    snapshot = empty()
    fetched = {}
    pending = new Map()
  }
  return { ...snapshot, errors: { ...snapshot.errors } }
}

// Cache only this dashboard's bounded summaries, isolated to the current login.
// Slow sources publish independently; refreshing never clears successful data.
export async function refreshDashboard(onChange: (value: DashboardSnapshot) => void): Promise<boolean> {
  getDashboardSnapshot()
  const token = owner
  const jobs: Array<{ key: ResourceKey; ttl: number; load: () => Promise<Partial<DashboardSnapshot>> }> = [
    { key: 'overview', ttl: 10000, load: async () => ({ overview: await getOverview() }) },
    { key: 'turns', ttl: 5000, load: async () => ({ turns: (await getTurns({ limit: 24 })).items }) },
    { key: 'scenarios', ttl: 10000, load: async () => ({ scenarios: (await getScenarios()).items }) },
    { key: 'logs', ttl: 5000, load: async () => ({ logs: (await queryLogs({ page: 1, page_size: 8, min_level: 'warning' })).items }) },
    { key: 'projects', ttl: 30000, load: async () => { const value = await listProjects({ page: 1, page_size: 8 }); return { projects: value.items, projectTotal: value.total } } },
  ]
  onChange(getDashboardSnapshot())
  await Promise.all(jobs.map(async ({ key, ttl, load }) => {
    if (Date.now() - (fetched[key] || 0) < ttl) return
    let request = pending.get(key)
    if (!request) {
      const requests = pending
      request = load().then(value => {
        if (owner !== token || getToken() !== token) return
        fetched[key] = Date.now()
        snapshot = { ...snapshot, ...value, lastUpdatedAt: fetched[key]!, errors: { ...snapshot.errors } }
        delete snapshot.errors[key]
      }, error => {
        if (owner !== token || getToken() !== token) return
        snapshot = { ...snapshot, errors: { ...snapshot.errors, [key]: error instanceof Error ? error.message : '加载失败' } }
      }).finally(() => { requests.delete(key) })
      requests.set(key, request)
    }
    await request
    if (owner === token && getToken() === token) onChange(getDashboardSnapshot())
  }))
  return owner === token && Object.keys(snapshot.errors).length === 0
}
