import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

async function fixture() {
  let now = 100000
  let token = 'first-user'
  let releaseOverview
  let failLogs = false
  const counts = {}
  const count = name => { counts[name] = (counts[name] || 0) + 1 }
  const mocks = {
    './http': { getToken: () => token },
    './observabilityService': {
      getOverview: async () => { count('overview'); await new Promise(resolve => { releaseOverview = resolve }); return { token: { total_calls: 42 } } },
      getScenarios: async () => { count('scenarios'); return { items: [{ task_type: 'scan' }] } },
      getTurns: async () => { count('turns'); return { items: [{ turn_id: 't' }] } },
      queryLogs: async () => { count('logs'); if (failLogs) throw new Error('offline'); return { items: [{ message: 'warning' }] } },
    },
    './projectService': { listProjects: async () => { count('projects'); return { items: [{ id: 'p' }], total: 1 } } },
  }
  const context = vm.createContext({ Date: { now: () => now } })
  const input = await readFile(new URL('../src/services/dashboardService.ts', import.meta.url), 'utf8')
  const code = ts.transpileModule(input, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
  const module = new vm.SourceTextModule(code, { context })
  await module.link(path => {
    const values = mocks[path]
    return new vm.SyntheticModule(Object.keys(values), function () {
      for (const [name, value] of Object.entries(values)) this.setExport(name, value)
    }, { context })
  })
  await module.evaluate()
  return {
    service: module.namespace, counts,
    release: () => releaseOverview(),
    advance: value => { now += value },
    login: value => { token = value },
    fail: () => { failLogs = true },
  }
}

test('fast panels render before overview and concurrent refreshes share requests', async () => {
  const f = await fixture()
  const updates = []
  const first = f.service.refreshDashboard(value => updates.push(value))
  const second = f.service.refreshDashboard(() => {})
  await new Promise(resolve => setImmediate(resolve))
  assert(updates.some(value => value.projects.length && !value.overview))
  assert.equal(f.counts.overview, 1)
  f.release()
  assert.equal(await first, true)
  await second
  await f.service.refreshDashboard(() => {})
  assert.equal(f.counts.overview, 1)
  assert.equal(f.counts.projects, 1)
  f.advance(6000)
  await f.service.refreshDashboard(() => {})
  assert.equal(f.counts.turns, 2)
  assert.equal(f.counts.projects, 1)
  assert.equal(f.counts.overview, 1)
})

test('failed refresh retains data and login switch rejects the old response', async () => {
  const f = await fixture()
  const first = f.service.refreshDashboard(() => {})
  f.release()
  await first
  f.advance(6000)
  f.fail()
  assert.equal(await f.service.refreshDashboard(() => {}), false)
  assert.equal(f.service.getDashboardSnapshot().logs.length, 1)
  f.advance(5000)
  const old = f.service.refreshDashboard(() => {})
  f.login('second-user')
  assert.equal(f.service.getDashboardSnapshot().overview, null)
  f.release()
  await old
  assert.equal(f.service.getDashboardSnapshot().overview, null)
  assert.equal(f.service.getDashboardSnapshot().projects.length, 0)
})
