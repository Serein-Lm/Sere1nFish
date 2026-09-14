import type { CollectRecordGroup } from './collectRecordUtils'

export default function CollectRecordTimes({ group }: { group: CollectRecordGroup }) {
  return <div className="collect-record-times">
    <span title={group.publishTimeSource}>发布：{group.publishTime || '时间待核实'}</span>
    {group.lastSeenTime && <span>最近采集：{group.lastSeenTime}（北京时间）</span>}
  </div>
}
