import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

async function fixture(library, options = []) {
  const calls = []
  const mocks = {
    './targetLibraryService': { getLibraryTarget: async () => library },
    './projectService': { getProject: async id => { calls.push(['project', id]); return { id } } },
    './sourceDocumentService': {
      listProjectTargetOptions: async id => { calls.push(['options', id]); return { items: options } },
      getProjectTargetDashboard: async (project, target) => { calls.push(['dashboard', project, target]); return { target: { target_id: target } } },
    },
  }
  const context = vm.createContext({})
  const input = await readFile(new URL('../src/services/targetDetailService.ts', import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(ts.transpileModule(input, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText, { context })
  await module.link(path => new vm.SyntheticModule(Object.keys(mocks[path]), function () {
    for (const [name, value] of Object.entries(mocks[path])) this.setExport(name, value)
  }, { context }))
  await module.evaluate()
  return { service: module.namespace, calls }
}

test('single identity opens only its project and dashboard without loading the target tree', async () => {
  const f = await fixture({ target_id: 't', member_target_ids: ['t'], projects: [{ project_id: 'p', active: true }] })
  const result = await f.service.loadTargetProjectContext('p', 't')
  assert.equal(result.dashboard.target.target_id, 't')
  assert.deepEqual(f.calls, [['project', 'p'], ['dashboard', 'p', 't']])
})

test('merged identities use the requested project member even if another unit has the same name', async () => {
  const f = await fixture({ target_id: 'canonical', member_target_ids: ['canonical', 'historical'], projects: [{ project_id: 'p', active: true }] }, [
    { target_id: 'unrelated', target_name: '相同名称' }, { target_id: 'historical', target_name: '相同名称' },
  ])
  const result = await f.service.loadTargetProjectContext('p', 'canonical')
  assert.equal(result.dashboard.target.target_id, 'historical')
  assert.deepEqual(f.calls.at(-1), ['dashboard', 'p', 'historical'])
})

test('explicit historical identity is retained when several members belong to the project', async () => {
  const f = await fixture({ target_id: 'canonical', member_target_ids: ['canonical', 'historical'], projects: [{ project_id: 'p', active: true }] }, [
    { target_id: 'canonical' }, { target_id: 'historical' },
  ])
  assert.equal((await f.service.loadTargetProjectContext('p', 'historical')).dashboard.target.target_id, 'historical')
})

test('unrelated and missing project identities do not query or silently display another target', async () => {
  const f = await fixture({ target_id: 't', member_target_ids: ['t', 'old'], projects: [{ project_id: 'p', active: true }] }, [{ target_id: 'unrelated' }])
  await assert.rejects(f.service.loadTargetProjectContext('other-project', 't'), /未关联/)
  assert.equal(f.calls.length, 0)
  await assert.rejects(f.service.loadTargetProjectContext('p', 't'), /没有可用/)
  assert.deepEqual(f.calls, [['options', 'p']])
})

test('inactive project membership preserves the library and does not fetch an active dashboard', async () => {
  const library = { target_id: 't', member_target_ids: ['t'], projects: [{ project_id: 'p', active: false }] }
  const f = await fixture(library)
  const result = await f.service.loadTargetProjectContext('p', 't')
  assert.equal(result.library, library)
  assert.equal(result.dashboard, null)
  assert.deepEqual(f.calls, [['project', 'p']])
})
