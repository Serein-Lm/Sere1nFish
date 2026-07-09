/**
 * 云手机操控 - 设备数据与 AI 助手服务
 *
 * 说明：真实的视频流操控与资源申请将在后续接入后端后替换。
 * 当前提供：
 *  1. 设备类型定义
 *  2. 可操控设备的 Mock 数据
 *  3. 自包含的「AI 助手」模拟流式响应（无需后端即可运行）
 */

// ============================================
// 设备类型
// ============================================

export type DeviceStatus = 'idle' | 'busy' | 'offline' | 'maintenance'
export type NetworkType = 'wifi' | '5g' | '4g'

export interface PhoneDevice {
  id: string
  name: string
  model: string
  osVersion: string
  resolution: string
  status: DeviceStatus
  battery: number
  network: NetworkType
  region: string
  latency: number
  cpu: number
  /** 当前占用者（busy 时存在） */
  occupant?: string
  /** 标签，例如已安装应用 / 养号状态 */
  tags: string[]
}

export const DEVICE_STATUS_META: Record<
  DeviceStatus,
  { label: string; color: string; dot: string }
> = {
  idle: { label: '空闲', color: 'success', dot: '#52c41a' },
  busy: { label: '使用中', color: 'processing', dot: '#1677ff' },
  offline: { label: '离线', color: 'default', dot: '#8c8c8c' },
  maintenance: { label: '维护中', color: 'warning', dot: '#faad14' },
}

// ============================================
// Mock 设备数据
// ============================================

export const MOCK_DEVICES: PhoneDevice[] = [
  {
    id: 'dev-001',
    name: '云机-华东-01',
    model: 'Pixel 7 Pro',
    osVersion: 'Android 14',
    resolution: '1440 × 3120',
    status: 'idle',
    battery: 92,
    network: 'wifi',
    region: '华东 · 上海',
    latency: 38,
    cpu: 12,
    tags: ['微信', '已养号', '高匿'],
  },
  {
    id: 'dev-002',
    name: '云机-华北-03',
    model: 'Redmi K60',
    osVersion: 'Android 13',
    resolution: '1220 × 2712',
    status: 'idle',
    battery: 78,
    network: '5g',
    region: '华北 · 北京',
    latency: 52,
    cpu: 9,
    tags: ['抖音', '小红书'],
  },
  {
    id: 'dev-003',
    name: '云机-华南-07',
    model: 'OnePlus 11',
    osVersion: 'Android 14',
    resolution: '1440 × 3216',
    status: 'busy',
    battery: 64,
    network: 'wifi',
    region: '华南 · 深圳',
    latency: 45,
    cpu: 47,
    occupant: 'operator_li',
    tags: ['微信', '企业微信'],
  },
  {
    id: 'dev-004',
    name: '云机-华东-09',
    model: 'Samsung S23',
    osVersion: 'Android 13',
    resolution: '1080 × 2340',
    status: 'idle',
    battery: 100,
    network: 'wifi',
    region: '华东 · 杭州',
    latency: 41,
    cpu: 6,
    tags: ['钉钉', '飞书', '已养号'],
  },
  {
    id: 'dev-005',
    name: '云机-西南-02',
    model: 'vivo X90',
    osVersion: 'Android 13',
    resolution: '1260 × 2800',
    status: 'maintenance',
    battery: 33,
    network: '4g',
    region: '西南 · 成都',
    latency: 120,
    cpu: 3,
    tags: ['微博'],
  },
  {
    id: 'dev-006',
    name: '云机-华北-11',
    model: 'OPPO Find X6',
    osVersion: 'Android 14',
    resolution: '1240 × 2772',
    status: 'busy',
    battery: 58,
    network: '5g',
    region: '华北 · 天津',
    latency: 60,
    cpu: 38,
    occupant: 'operator_wang',
    tags: ['抖音', '快手'],
  },
  {
    id: 'dev-007',
    name: '云机-华东-14',
    model: 'Pixel 8',
    osVersion: 'Android 15',
    resolution: '1080 × 2400',
    status: 'idle',
    battery: 88,
    network: 'wifi',
    region: '华东 · 南京',
    latency: 36,
    cpu: 8,
    tags: ['微信', 'Telegram', '高匿'],
  },
  {
    id: 'dev-008',
    name: '云机-海外-01',
    model: 'Galaxy A54',
    osVersion: 'Android 14',
    resolution: '1080 × 2340',
    status: 'offline',
    battery: 0,
    network: '4g',
    region: '海外 · 香港',
    latency: 0,
    cpu: 0,
    tags: ['WhatsApp', 'Telegram'],
  },
]

// ============================================
// AI 助手 - 模拟流式响应
// ============================================

export interface QuickPrompt {
  key: string
  label: string
  description: string
  icon?: string
}

/** 根据是否已连接设备，返回上下文相关的快捷建议 */
export function getQuickPrompts(device: PhoneDevice | null): QuickPrompt[] {
  if (!device) {
    return [
      { key: 'how-to', label: '如何开始', description: '介绍云手机操控台的使用流程' },
      { key: 'pick', label: '设备选择建议', description: '帮我推荐一台适合做微信操作的空闲设备' },
    ]
  }
  return [
    { key: 'analyze', label: '分析当前屏幕', description: '分析当前设备屏幕内容，并指出可执行的操作' },
    { key: 'next', label: '下一步操作建议', description: '基于当前进度，给出下一步的操作建议' },
    { key: 'script', label: '生成沟通话术', description: '帮我生成一段自然可信的微信开场话术' },
    { key: 'risk', label: '风险与合规提醒', description: '当前操作有哪些风控风险，如何规避账号异常' },
  ]
}

/** 生成上下文相关的回复文本（Markdown） */
function buildResponse(prompt: string, device: PhoneDevice | null): string {
  const p = prompt.toLowerCase()
  const deviceName = device ? `\`${device.name}\`（${device.model} · ${device.osVersion}）` : '当前设备'

  if (!device) {
    if (p.includes('设备') || p.includes('推荐') || p.includes('选择') || p.includes('pick')) {
      return [
        '为微信类操作推荐 **空闲 + 已养号 + 高匿** 的设备，优先级如下：',
        '',
        '1. **云机-华东-01** · Pixel 7 Pro · 延迟 38ms · 标签含「已养号 / 高匿」',
        '2. **云机-华东-14** · Pixel 8 · 延迟 36ms · 标签含「已养号 / 高匿」',
        '',
        '> 建议避开延迟 > 80ms 或电量 < 30% 的设备，操作流畅度与稳定性更佳。',
        '',
        '在左侧设备列表点击 **接入控制** 即可开始。',
      ].join('\n')
    }
    return [
      '### 云手机操控台使用流程',
      '',
      '1. **选择设备** — 在左侧挑选一台 `空闲` 状态的云手机，点击「接入控制」。',
      '2. **实时操控** — 通过视频流画面直接点击、滑动、输入，如同操作本地手机。',
      '3. **AI 协同** — 我会根据屏幕进度，实时提供操作建议、话术与风险提醒。',
      '',
      '现在先从左侧选一台空闲设备接入吧 👈',
    ].join('\n')
  }

  if (p.includes('话术') || p.includes('开场') || p.includes('script') || p.includes('沟通')) {
    return [
      `已结合 ${deviceName} 的可用应用，生成一段微信开场话术：`,
      '',
      '> 您好，看到您之前关注过我们行业的活动～ 我这边是负责对接资料发放的，方便加您一下吗？有一份整理好的资料想同步给您。',
      '',
      '**要点说明**',
      '',
      '- **降低戒备**：以「资料发放 / 活动对接」为由，弱化推销感。',
      '- **制造合理性**：引用「之前关注过」建立熟悉感。',
      '- **引导动作**：明确请求「加好友」，给出清晰的下一步。',
      '',
      '需要我再生成 **2 ~ 3 个不同风格**（正式 / 轻松 / 客服）的版本吗？',
    ].join('\n')
  }

  if (p.includes('分析') || p.includes('屏幕') || p.includes('截图') || p.includes('analyze')) {
    return [
      `正在分析 ${deviceName} 的当前画面……`,
      '',
      '**识别结果**',
      '',
      '- 当前位于 **系统桌面**，检测到微信、抖音等常用应用图标。',
      '- 顶部状态栏：信号良好，电量充足，无异常弹窗。',
      '',
      '**可执行操作**',
      '',
      '1. 点击「微信」图标进入，准备发起会话。',
      '2. 或先打开「设置」确认代理与时区，降低风控概率。',
      '',
      '需要我引导你完成 **打开微信 → 进入指定会话** 的完整路径吗？',
    ].join('\n')
  }

  if (p.includes('风险') || p.includes('风控') || p.includes('合规') || p.includes('安全') || p.includes('risk')) {
    return [
      `针对 ${deviceName} 的当前操作，风险提示如下：`,
      '',
      '| 风险项 | 等级 | 规避建议 |',
      '| --- | --- | --- |',
      '| 操作频率过高 | 中 | 单次会话间隔 > 30s，模拟真人节奏 |',
      '| 新号高频加好友 | 高 | 当日主动添加控制在 5 ~ 8 人以内 |',
      '| 设备指纹一致 | 中 | 使用高匿设备，保持独立代理出口 |',
      '',
      '> 当前设备已标记「高匿」，建议继续保持低频、拟人化操作。',
    ].join('\n')
  }

  if (p.includes('下一步') || p.includes('建议') || p.includes('next')) {
    return [
      `基于 ${deviceName} 的当前进度，建议下一步：`,
      '',
      '1. **进入目标会话** — 打开微信，定位到目标联系人。',
      '2. **发送开场白** — 使用已生成的话术，注意首句不要带链接。',
      '3. **观察回应** — 等待对方回复后再推进，避免连续多条消息。',
      '',
      '要我直接 **生成本轮对应的话术** 并准备好发送内容吗？',
    ].join('\n')
  }

  if (p.includes('微信') || p.includes('好友') || p.includes('添加')) {
    return [
      `在 ${deviceName} 上执行「微信加好友」的推荐路径：`,
      '',
      '1. 桌面点击 **微信** → 右上角 **+** → **添加朋友**。',
      '2. 搜索手机号 / 微信号，进入资料页点击 **添加到通讯录**。',
      '3. 验证消息使用自然话术，**不要群发**。',
      '',
      '> 提示：新号当日主动添加建议 ≤ 8 人，且穿插浏览朋友圈等拟人行为。',
    ].join('\n')
  }

  return [
    `收到，我会基于 ${deviceName} 协助你。`,
    '',
    '我可以帮你：',
    '',
    '- **分析屏幕** 当前画面并给出可执行操作',
    '- **生成话术** 自然可信的沟通内容',
    '- **规划路径** 完成指定任务的操作步骤',
    '- **风险提醒** 实时规避账号风控',
    '',
    '直接告诉我你想做什么即可，例如「帮我生成微信开场话术」。',
  ].join('\n')
}

/**
 * 模拟流式输出：逐段返回累积内容，无需后端。
 * @param onToken 每次回调返回「累积」后的完整内容
 * @param signal  可中断
 */
export async function streamAISuggestion(
  prompt: string,
  device: PhoneDevice | null,
  onToken: (full: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const full = buildResponse(prompt, device)
  // 按字符切片，营造打字机效果
  const step = 2
  let acc = ''

  // 起始思考延迟
  await delay(380, signal)

  for (let i = 0; i < full.length; i += step) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    acc = full.slice(0, i + step)
    onToken(acc)
    // 换行处略作停顿，更自然
    const isBreak = full[i] === '\n'
    await delay(isBreak ? 26 : 12, signal)
  }
  onToken(full)
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const t = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(t)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}
