import type { Task, Finding, FindingCopywriting } from './taskService'

// ============================================
// Mock 任务列表
// ============================================
export const MOCK_TASKS: Task[] = [
  {
    task_id: 'task_url_001',
    project_id: 'proj_001',
    task_type: 'url_scan',
    params: { urls: ['https://www.example-tech.com'] },
    status: 'completed',
    progress: {},
    error: null,
    created_at: '2026-03-26T10:00:00',
    updated_at: '2026-03-26T10:05:32',
  },
  {
    task_id: 'task_url_002',
    project_id: 'proj_001',
    task_type: 'url_scan',
    params: { urls: ['https://careers.example-tech.com'] },
    status: 'running',
    progress: {},
    error: null,
    created_at: '2026-03-26T11:20:00',
    updated_at: '2026-03-26T11:20:45',
  },
  {
    task_id: 'task_xhs_001',
    project_id: 'proj_001',
    task_type: 'xhs_search',
    params: { keyword: '星辰科技 产品经理', max_notes: 20 },
    status: 'completed',
    progress: {},
    error: null,
    created_at: '2026-03-25T14:00:00',
    updated_at: '2026-03-25T14:12:00',
  },
  {
    task_id: 'task_tag_001',
    project_id: 'proj_001',
    task_type: 'web_tagging',
    params: { company_name: '星辰科技有限公司' },
    status: 'error',
    progress: {},
    error: 'Hunter API 配额已用尽',
    created_at: '2026-03-24T09:00:00',
    updated_at: '2026-03-24T09:01:00',
  },
]

// ============================================
// Mock Findings
// ============================================
export const MOCK_FINDINGS: Finding[] = [
  {
    finding_id: 'f_001',
    task_id: 'task_url_001',
    project_id: 'proj_001',
    url: 'https://www.example-tech.com',
    type: 'hr_contact',
    channel: 'email',
    role: 'hr',
    label: '简历投递邮箱',
    value: 'hr@example-tech.com',
    context: '首页 Footer > 加入我们 > 简历投递',
    evidence: '页面底部显示：简历投递请发送至 hr@example-tech.com',
    attention_score: 92,
    attention_reason: '可直接触达的HR招聘邮箱，高价值社工入口',
  },
  {
    finding_id: 'f_002',
    task_id: 'task_url_001',
    project_id: 'proj_001',
    url: 'https://www.example-tech.com/contact',
    type: 'business_contact',
    channel: 'phone',
    role: 'sales',
    label: '商务合作热线',
    value: '400-888-9999',
    context: '联系我们页面 > 商务合作模块',
    evidence: '商务合作请拨打：400-888-9999',
    attention_score: 78,
    attention_reason: '商务热线可用于供应商伪装场景',
  },
  {
    finding_id: 'f_003',
    task_id: 'task_url_001',
    project_id: 'proj_001',
    url: 'https://www.example-tech.com',
    type: 'customer_service',
    channel: 'wechat',
    role: 'support',
    label: '客服微信',
    value: 'ExampleTech_CS',
    context: '首页右下角悬浮窗 > 在线客服 > 添加微信',
    evidence: '扫码添加客服微信：ExampleTech_CS',
    attention_score: 85,
    attention_reason: '微信客服可建立直接对话，适合社工切入',
  },
  {
    finding_id: 'f_004',
    task_id: 'task_url_001',
    project_id: 'proj_001',
    url: 'https://www.example-tech.com/about',
    type: 'social_media',
    channel: 'other',
    role: 'marketing',
    label: '官方抖音号',
    value: '@ExampleTech官方',
    context: '关于我们 > 社交媒体 > 抖音',
    evidence: '关注我们的抖音号 @ExampleTech官方',
    attention_score: 45,
    attention_reason: '社交媒体账号可用于信息收集',
  },
  {
    finding_id: 'f_005',
    task_id: 'task_url_001',
    project_id: 'proj_001',
    url: 'https://www.example-tech.com/support',
    type: 'tech_support',
    channel: 'form',
    role: 'it',
    label: '技术支持工单',
    value: 'https://support.example-tech.com/ticket',
    context: '技术支持页面 > 提交工单',
    evidence: '遇到技术问题？请提交工单，我们将在24小时内回复',
    attention_score: 65,
    attention_reason: '工单系统可用于IT支持伪装场景',
  },
]

// ============================================
// Mock Copywriting — 微信话术 (f_003)
// ============================================
const WECHAT_COPYWRITING: FindingCopywriting = {
  finding_id: 'f_003',
  url: 'https://www.example-tech.com',
  finding_type: 'customer_service',
  finding_channel: 'wechat',
  finding_label: '客服微信',
  finding_value: 'ExampleTech_CS',
  scenario: {
    scenario_name: '供应商合作咨询',
    target_background: '目标为星辰科技客服人员，负责日常客户咨询和售后支持，对公司内部架构有一定了解',
    scenario_overview: '以供应商身份添加客服微信，通过合作咨询逐步获取内部联系方式和组织架构信息',
    faked_identity: {
      name: '李明远',
      company: '鼎信数据科技有限公司',
      company_desc: '专注企业级数据安全解决方案的科技公司',
      position: '商务拓展经理',
      background: '5年ToB销售经验，曾服务多家互联网企业',
      personality: '专业、热情、善于沟通，略带急迫感',
    },
    logic_chain: [
      { step: 1, channel: 'wechat', action: '添加客服微信，以合作咨询为由建立联系', fallback: '若被拒绝，换用邮件渠道联系' },
      { step: 2, channel: 'wechat', action: '通过产品咨询建立信任，获取内部对接人信息', fallback: '若客服警觉，转为纯产品咨询降低戒心' },
      { step: 3, channel: 'email', action: '向获取的内部联系人发送合作方案', fallback: null },
    ],
    risk_notes: '注意不要过早暴露真实意图，保持供应商人设的一致性',
  },
  scripts: [
    {
      channel: 'wechat',
      dialogue: [
        { role: 'attacker', content: '您好，我是鼎信数据的李明远，在贵司官网看到客服微信就加了。我们公司最近在做企业数据安全方面的解决方案，想了解一下贵司在这块有没有合作的可能？', tactic: '互惠原则' },
        { role: 'target', content: '您好李经理，感谢关注。请问您具体想了解哪方面的合作呢？', tactic: null },
        { role: 'attacker', content: '是这样的，我们有一套针对中大型企业的数据防泄漏方案，之前给腾讯和美团都做过部署。最近听说贵司在扩展安全团队，想看看有没有机会聊聊。', tactic: '权威效应' },
        { role: 'target', content: '好的，这块的话可能需要我们技术部门来对接。我帮您问一下相关负责人。', tactic: null },
        { role: 'attacker', content: '太好了，麻烦您了！对了，方便的话能告诉我技术部门负责采购的是哪位吗？我这边也好提前准备一份针对性的方案。', tactic: '合理化' },
        { role: 'target', content: '技术采购这块一般是张工在负责，我把他的企业微信推给您吧。', tactic: null },
        { role: 'attacker', content: '感谢感谢！那我先准备下方案材料，到时候直接和张工沟通。另外贵司现在用的是什么安全产品呀？我好做个对比分析。', tactic: '社会认同' },
        { role: 'target', content: '目前用的是某某安全的产品，具体型号我不太清楚，您可以问张工。', tactic: null },
        { role: 'attacker', content: '了解了解，那个产品我们很熟悉。我这就整理一份对比报告，明天发给张工。再次感谢您的帮助！🙏', tactic: '互惠原则' },
      ],
      email_template: null,
      key_points: ['以供应商身份建立信任', '利用权威背书（腾讯、美团）增加可信度', '自然过渡到获取内部联系人', '获取现有安全产品信息'],
    },
  ],
  payload: null,
  objections: [
    { objection: '我们暂时没有这方面的需求', response: '理解理解，不过我们最近刚帮一家和贵司规模类似的公司做了安全评估，发现了不少潜在风险。我可以免费给贵司做一次安全体检，不需要任何承诺。', tactic: '互惠原则', context_note: '利用免费服务降低对方戒心' },
    { objection: '你怎么知道我们在扩展安全团队？', response: '哈哈，行业圈子不大，之前在一个安全峰会上听同行提到的。而且贵司最近在招安全工程师，我在Boss直聘上看到了。', tactic: '合理化', context_note: '用公开信息佐证，避免引起怀疑' },
    { objection: '我需要先和领导确认一下', response: '当然没问题，您看这样行不行，我先发一份简要的方案概述给您，您转给领导看看，有兴趣我们再深入聊。这样也不耽误您太多时间。', tactic: '一致性', context_note: '降低决策门槛，推动流程前进' },
  ],
  target_analysis: '客服人员通常对外部合作咨询持开放态度，且有义务将商务需求转接给相关部门，是获取内部联系方式的理想切入点。',
  psychology_strategy: '互惠原则 + 权威效应：先提供价值（免费安全评估），再借助知名客户背书建立专业形象。',
  case_reference: '参考2025年某安全公司供应链攻击案例，通过客服渠道获取内部通讯录。',
  loaded_skills: ['wechat', 'vendor'],
  status: 'completed',
  error: null,
}

// ============================================
// Mock Copywriting — 邮件话术 (f_001)
// ============================================
const EMAIL_COPYWRITING: FindingCopywriting = {
  finding_id: 'f_001',
  url: 'https://www.example-tech.com',
  finding_type: 'hr_contact',
  finding_channel: 'email',
  finding_label: '简历投递邮箱',
  finding_value: 'hr@example-tech.com',
  scenario: {
    scenario_name: '高端人才求职',
    target_background: 'HR负责招聘，日常处理大量简历邮件，对求职类邮件警惕性较低',
    scenario_overview: '伪装为行业资深人才投递简历，附件中携带特制文档，利用HR打开简历的习惯实现初始访问',
    faked_identity: {
      name: '陈思远',
      company: '前微软亚洲研究院',
      company_desc: '',
      position: '高级算法工程师',
      background: '8年AI/ML经验，ACM金牌，前微软研究员',
      personality: '低调专业，措辞简洁，技术范十足',
    },
    logic_chain: [
      { step: 1, channel: 'email', action: '发送求职邮件+简历附件至HR邮箱', fallback: '若无回复，3天后跟进一封简短询问邮件' },
      { step: 2, channel: 'phone', action: '电话跟进确认简历是否收到', fallback: null },
    ],
    risk_notes: '简历文件名使用真实姓名格式，避免使用可疑文件名',
  },
  scripts: [
    {
      channel: 'email',
      dialogue: [
        { role: 'attacker', content: '发送求职邮件', tactic: null },
      ],
      email_template: `收件人：hr@example-tech.com
发件人：chensiyuan.career@outlook.com
主题：【求职】高级算法工程师 - 陈思远 | 前微软亚研院 · 8年AI经验

尊敬的HR：

您好！我是陈思远，目前在考虑新的职业机会，在贵司官网看到正在招聘AI方向的技术人才，非常感兴趣。

简要背景：
• 8年AI/机器学习领域经验
• 前微软亚洲研究院高级研究员（2020-2025）
• ACM-ICPC亚洲区域赛金牌
• 主导过多个千万级DAU产品的推荐系统架构
• 在NeurIPS、ICML发表论文6篇

我对贵司在智能安全领域的技术方向非常认可，相信我的经验能为团队带来价值。

附件为我的详细简历，期待有机会进一步沟通。

此致
陈思远
手机：138-0000-1234
LinkedIn：linkedin.com/in/chensiyuan-ai`,
      key_points: ['利用HR对简历邮件的低警惕性', '知名公司背景增加可信度', '附件为攻击载荷入口'],
    },
    {
      channel: 'phone',
      dialogue: [
        { role: 'attacker', content: '您好，请问是星辰科技人力资源部吗？', tactic: null },
        { role: 'target', content: '是的，请问有什么事？', tactic: null },
        { role: 'attacker', content: '我是陈思远，前两天给贵司HR邮箱投了一份简历，想确认一下是否收到了？我投的是高级算法工程师的岗位。', tactic: '合理化' },
        { role: 'target', content: '稍等我查一下……您说您叫陈思远是吧？', tactic: null },
        { role: 'attacker', content: '对的，邮件主题写的是"高级算法工程师 - 陈思远"，附件有我的详细简历。', tactic: '一致性' },
        { role: 'target', content: '找到了，我看到了您的邮件。简历我们会转给技术部门评估，有进展会联系您。', tactic: null },
        { role: 'attacker', content: '好的，非常感谢！方便的话能告诉我大概多久会有反馈吗？我目前也在和其他几家公司沟通，想合理安排一下时间。', tactic: '稀缺性' },
        { role: 'target', content: '一般一周左右，如果技术面试官觉得合适会直接联系您。', tactic: null },
        { role: 'attacker', content: '明白了，那我等您消息。谢谢！', tactic: null },
      ],
      email_template: null,
      key_points: ['电话跟进确保HR打开附件', '利用稀缺性（其他offer）制造紧迫感', '获取内部流程信息'],
    },
  ],
  payload: {
    archive_name: '陈思远_简历_2026.zip',
    exe_name: '陈思远_简历_详细版.pdf.exe',
    icon_disguise: 'PDF图标',
    compression_method: 'zip_double',
    password: 'resume2026',
    notes: '双重压缩绕过邮件网关检测，密码在邮件正文中以"附件密码"形式提供',
  },
  objections: [
    { objection: '我们目前没有在招这个岗位', response: '是吗？我在Boss直聘上看到贵司还挂着AI算法工程师的JD，可能是还没来得及下架？不过没关系，如果后续有合适的机会也可以考虑我，简历您先留着。', tactic: '合理化', context_note: '用公开招聘信息反驳' },
    { objection: '请通过我们官方招聘渠道投递', response: '好的，我之前在官网投过但一直没收到回复，所以才直接发邮件的。我这就再通过官方渠道投一次，麻烦您帮忙留意一下。', tactic: '合理化', context_note: '解释直接发邮件的合理性' },
  ],
  target_analysis: 'HR每天处理大量求职邮件，对简历附件的警惕性远低于其他类型附件。知名公司背景+高学历标签会显著提升简历被打开的概率。',
  psychology_strategy: '权威效应 + 稀缺性：微软研究院背景建立权威感，"其他公司也在谈"制造紧迫感促使HR尽快处理。',
  case_reference: '参考APT组织Lazarus通过LinkedIn虚假招聘进行的社工攻击手法。',
  loaded_skills: ['email', 'recruitment'],
  status: 'completed',
  error: null,
}

// ============================================
// Mock Copywriting — 电话话术 (f_002)
// ============================================
const PHONE_COPYWRITING: FindingCopywriting = {
  finding_id: 'f_002',
  url: 'https://www.example-tech.com/contact',
  finding_type: 'business_contact',
  finding_channel: 'phone',
  finding_label: '商务合作热线',
  finding_value: '400-888-9999',
  scenario: {
    scenario_name: 'IT审计供应商回访',
    target_background: '前台/商务接线员，负责转接电话和初步咨询，对公司组织架构有基本了解',
    scenario_overview: '伪装为IT审计公司回访人员，以年度安全合规检查为由获取IT部门联系方式和内部系统信息',
    faked_identity: {
      name: '王浩然',
      company: '中信安达信息安全评估中心',
      company_desc: '国家认证的信息安全等级保护测评机构',
      position: '高级安全审计师',
      background: 'CISP认证，10年安全审计经验',
      personality: '严肃专业，语速适中，带有官方口吻',
    },
    logic_chain: [
      { step: 1, channel: 'phone', action: '拨打商务热线，要求转接IT部门或安全负责人', fallback: '若前台拒绝转接，要求留下IT部门邮箱' },
      { step: 2, channel: 'email', action: '向IT部门发送"审计通知"邮件', fallback: null },
      { step: 3, channel: 'phone', action: '跟进电话确认邮件收到并安排"远程审计"', fallback: null },
    ],
    risk_notes: '注意使用专业术语但不要过度，避免引起IT人员的技术质疑',
  },
  scripts: [
    {
      channel: 'phone',
      dialogue: [
        { role: 'attacker', content: '您好，这里是中信安达信息安全评估中心，我是高级审计师王浩然，工号AUD-2026-0847。请问是星辰科技吗？', tactic: '权威效应' },
        { role: 'target', content: '是的，请问有什么事？', tactic: null },
        { role: 'attacker', content: '是这样的，贵公司去年委托我们做的等保2.0测评，按照规定今年需要进行年度复查。我这边需要和贵司的信息安全负责人或IT部门对接一下，安排复查的具体时间。', tactic: '权威效应' },
        { role: 'target', content: '这个……我不太清楚这个事情，我帮您转一下IT部门吧。', tactic: null },
        { role: 'attacker', content: '好的，麻烦您了。对了，请问贵司IT部门负责安全这块的是哪位？我查一下去年的对接记录。', tactic: '合理化' },
        { role: 'target', content: '安全这块好像是刘工在负责，刘建国。', tactic: null },
        { role: 'attacker', content: '刘建国刘工，好的我记一下。那麻烦您帮我转过去，如果刘工不在的话，能给我一个他的直线或者手机号吗？因为这个复查有时间节点要求，月底之前必须完成。', tactic: '紧迫感' },
        { role: 'target', content: '好的，我先帮您转，如果没人接我把他办公室电话给您。', tactic: null },
        { role: 'attacker', content: '非常感谢您的配合。另外提醒一下，复查期间可能需要贵司提供一些系统配置信息和网络拓扑图，您可以提前和刘工说一声，让他准备一下。', tactic: '合理化' },
        { role: 'target', content: '好的，我会转达的。稍等，我帮您转接。', tactic: null },
      ],
      email_template: null,
      key_points: ['使用工号增加官方感', '利用等保合规的紧迫性', '自然获取IT负责人姓名和联系方式', '为后续索要敏感信息做铺垫'],
    },
  ],
  payload: null,
  objections: [
    { objection: '我们没有委托过你们做等保测评', response: '这样啊，那可能是我这边记录有误。不过按照网信办的要求，贵司这个规模是必须做等保的。要不这样，我把我们的资质证书和去年的测评报告编号发给贵司IT部门核实一下？', tactic: '权威效应', context_note: '用监管要求施压，同时提供"核实"机会降低怀疑' },
    { objection: '请发正式函件过来', response: '没问题，正式通知函我们会通过EMS寄送。但因为月底是截止日期，我建议先通过邮件发一份电子版给贵司IT部门，纸质版随后寄到。请问IT部门的邮箱是？', tactic: '紧迫感', context_note: '利用时间压力获取邮箱' },
    { objection: '我需要和领导确认', response: '理解，合规检查确实需要走流程。不过提醒您，如果超过截止日期未完成复查，按照《网络安全法》可能会影响贵司的等保证书续期。建议尽快确认。', tactic: '紧迫感', context_note: '用法规后果制造压力' },
  ],
  target_analysis: '前台/接线员通常不具备判断安全审计真伪的能力，且有义务将专业咨询转接给相关部门。利用等保合规的官方性质可以有效绕过初步筛查。',
  psychology_strategy: '权威效应 + 紧迫感：以国家认证机构身份建立权威，用合规截止日期制造时间压力。',
  case_reference: '参考社工攻击中经典的"IT审计"话术模板，结合国内等保2.0政策背景。',
  loaded_skills: ['phone', 'it_support'],
  status: 'completed',
  error: null,
}

// ============================================
// Mock Copywriting — 短信话术 (f_004 repurposed)
// ============================================
const SMS_COPYWRITING: FindingCopywriting = {
  finding_id: 'f_004',
  url: 'https://www.example-tech.com/about',
  finding_type: 'social_media',
  finding_channel: 'other',
  finding_label: '官方抖音号',
  finding_value: '@ExampleTech官方',
  scenario: {
    scenario_name: '活动中奖通知',
    target_background: '通过抖音互动获取的目标手机号，目标曾参与星辰科技线上活动',
    scenario_overview: '以官方活动中奖为由发送短信，引导目标点击链接领取"奖品"',
    faked_identity: {
      name: '星辰科技官方',
      company: '星辰科技有限公司',
      company_desc: '',
      position: '活动运营',
      background: '',
      personality: '官方、简洁、带有紧迫感',
    },
    logic_chain: [
      { step: 1, channel: 'sms', action: '发送中奖通知短信，附带领奖链接', fallback: '若未点击，24小时后发送"即将过期"提醒' },
      { step: 2, channel: 'wechat', action: '引导添加"客服微信"确认身份', fallback: null },
    ],
    risk_notes: '短信内容需模拟真实企业短信格式，包含退订提示',
  },
  scripts: [
    {
      channel: 'sms',
      dialogue: [
        { role: 'attacker', content: '【星辰科技】恭喜您在"2026星辰科技春季嘉年华"活动中获得二等奖（价值599元蓝牙耳机）！请在48小时内点击链接领取：https://act.example-tech.cn/prize?id=x8k2m 如有疑问请致电400-888-9999。退订回T', tactic: '虚荣心' },
        { role: 'target', content: '这是真的吗？我确实参加过他们的活动', tactic: null },
        { role: 'attacker', content: '【星辰科技】温馨提醒：您的春季嘉年华二等奖领取即将到期（剩余12小时），请尽快完成领取，逾期视为自动放弃。领取链接：https://act.example-tech.cn/prize?id=x8k2m', tactic: '紧迫感' },
      ],
      email_template: null,
      key_points: ['模拟真实企业短信格式', '包含真实客服电话增加可信度', '利用时间限制制造紧迫感', '退订提示增加真实感'],
    },
  ],
  payload: null,
  objections: [
    { objection: '我没有参加过这个活动', response: '（不回复，避免暴露。短信钓鱼以广撒网为主，不做个别跟进）', tactic: '合理化', context_note: '短信场景下不宜过多互动' },
  ],
  target_analysis: '短信钓鱼利用人们对"中奖"的好奇心和贪念，配合真实的企业信息（客服电话、活动名称）大幅提升点击率。',
  psychology_strategy: '虚荣心 + 紧迫感：中奖满足虚荣心，倒计时制造紧迫感促使快速行动。',
  case_reference: '参考2025年多起企业活动钓鱼短信案例。',
  loaded_skills: ['sms'],
  status: 'completed',
  error: null,
}

// ============================================
// Mock Copywriting — 内网钓鱼话术 (f_005)
// ============================================
const INTRANET_COPYWRITING: FindingCopywriting = {
  finding_id: 'f_005',
  url: 'https://www.example-tech.com/support',
  finding_type: 'tech_support',
  finding_channel: 'form',
  finding_label: '技术支持工单',
  finding_value: 'https://support.example-tech.com/ticket',
  scenario: {
    scenario_name: 'IT部门安全升级通知',
    target_background: '已获取内网邮箱或企业IM账号，目标为普通员工',
    scenario_overview: '伪装为IT部门发送内部通知，以系统安全升级为由引导员工访问钓鱼页面并输入凭据',
    faked_identity: {
      name: '信息技术部',
      company: '星辰科技有限公司',
      company_desc: '',
      position: 'IT运维组',
      background: '',
      personality: '官方、严肃、技术性',
    },
    logic_chain: [
      { step: 1, channel: 'intranet', action: '通过企业IM/内部邮件发送安全升级通知', fallback: '若员工未操作，次日发送"最后提醒"' },
      { step: 2, channel: 'intranet', action: '员工点击链接后进入伪造的SSO登录页', fallback: null },
    ],
    risk_notes: '内部通知需完全模拟公司IT部门的发文风格和格式',
  },
  scripts: [
    {
      channel: 'intranet',
      dialogue: [
        { role: 'attacker', content: '📢 【IT通知】关于开展2026年Q1信息系统安全升级的通知\n\n各位同事：\n\n根据公司信息安全管理要求，信息技术部将于本周对全司OA系统、邮件系统进行安全升级。为确保升级顺利完成，请所有员工在3月28日18:00前完成以下操作：\n\n1. 点击以下链接验证您的账户信息\n2. 按提示完成密码强度检测\n3. 确认双因素认证设置\n\n🔗 验证链接：https://sso-verify.example-tech.cn/auth\n\n⚠️ 注意事项：\n• 未在截止时间前完成验证的账户将被临时锁定\n• 如遇问题请联系IT服务台（分机：8001）\n• 本次升级不会影响正常办公\n\n信息技术部\n2026年3月26日', tactic: '权威效应' },
        { role: 'target', content: '收到，我现在就去验证。', tactic: null },
        { role: 'attacker', content: '📢 【提醒】安全升级验证截止提醒\n\n以下同事尚未完成账户验证，请在今日18:00前完成，否则账户将被临时锁定：\n\n• 张三（产品部）\n• 李四（市场部）\n• 王五（研发部）\n...\n\n验证链接：https://sso-verify.example-tech.cn/auth\n\n如已完成请忽略此消息。\n\n信息技术部', tactic: '紧迫感' },
      ],
      email_template: null,
      key_points: ['完全模拟IT部门发文格式', '利用"账户锁定"威胁制造紧迫感', '提供真实的IT服务台分机号增加可信度', '点名提醒增加社会压力'],
    },
  ],
  payload: null,
  objections: [
    { objection: '这个链接看起来不太对', response: '（以IT部门口吻回复）这是我们新部署的统一认证平台域名，和之前的SSO地址不同是正常的。如果您不放心，可以拨打IT服务台8001确认。', tactic: '权威效应', context_note: '利用IT部门权威消除疑虑' },
    { objection: '我之前没收到过这种通知', response: '这是今年新增的安全合规要求，之前确实没有做过。公司通过了ISO27001认证后，每季度都需要做一次账户安全验证。', tactic: '合理化', context_note: '用合规要求解释新流程' },
  ],
  target_analysis: '普通员工对IT部门的通知通常不会质疑，尤其是涉及"账户锁定"等后果时会快速执行。内部通知的可信度远高于外部邮件。',
  psychology_strategy: '权威效应 + 紧迫感 + 社会认同：IT部门权威 + 截止日期压力 + 点名列表的社会压力三重叠加。',
  case_reference: '参考多起企业内部钓鱼演练中IT通知类钓鱼的高成功率案例。',
  loaded_skills: ['intranet', 'it_support'],
  status: 'completed',
  error: null,
}

// ============================================
// 导出
// ============================================
export const MOCK_COPYWRITINGS: FindingCopywriting[] = [
  EMAIL_COPYWRITING,
  PHONE_COPYWRITING,
  WECHAT_COPYWRITING,
  SMS_COPYWRITING,
  INTRANET_COPYWRITING,
]

export function getMockCopywritingByFindingId(findingId: string): FindingCopywriting | undefined {
  return MOCK_COPYWRITINGS.find(c => c.finding_id === findingId)
}

export function getMockCopywritingsForTask(_taskId: string): FindingCopywriting[] {
  return MOCK_COPYWRITINGS
}

// ============================================
// Mock 统计数据
// ============================================
export const MOCK_GLOBAL_STATS = {
  total_calls: 156,
  total_input_tokens: 482000,
  total_output_tokens: 128500,
  total_tokens: 610500,
  total_cost_yuan: 4.8732,
  total_duration_ms: 342000,
  by_model: {
    'qwen3-max': { calls: 98, input_tokens: 320000, output_tokens: 85000, cost_yuan: 3.12 },
    'qwen3-plus': { calls: 42, input_tokens: 120000, output_tokens: 32000, cost_yuan: 1.28 },
    'deepseek-v3': { calls: 16, input_tokens: 42000, output_tokens: 11500, cost_yuan: 0.47 },
  },
  by_phase: {
    'scan': { calls: 45, input_tokens: 135000, output_tokens: 28000, cost_yuan: 1.05 },
    'scenario': { calls: 38, input_tokens: 128000, output_tokens: 42000, cost_yuan: 1.52 },
    'script': { calls: 52, input_tokens: 156000, output_tokens: 45000, cost_yuan: 1.68 },
    'objection': { calls: 21, input_tokens: 63000, output_tokens: 13500, cost_yuan: 0.63 },
  },
  by_agent: {
    'url_scanner': { calls: 30, input_tokens: 90000, output_tokens: 18000, cost_yuan: 0.72 },
    'web_tagging': { calls: 25, input_tokens: 75000, output_tokens: 20000, cost_yuan: 0.85 },
    'copywriter': { calls: 52, input_tokens: 156000, output_tokens: 45000, cost_yuan: 1.68 },
    'xhs_agent': { calls: 28, input_tokens: 98000, output_tokens: 28000, cost_yuan: 0.95 },
    'douyin_agent': { calls: 21, input_tokens: 63000, output_tokens: 17500, cost_yuan: 0.65 },
  },
}
