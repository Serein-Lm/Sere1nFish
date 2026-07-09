export const WEB_TAGGING_TYPE_MAP: Record<string, string> = {
  personal_mobile: '个人手机号',
  personal_email: '个人邮箱',
  personal_wechat: '个人微信（号/二维码）',
  enterprise_wechat: '企业微信（客服/二维码/添加入口）',
  hr_contact: '招聘/HR 联系方式',
  business_contact: '商务/销售/合作联系方式',
  media_contact: '媒体/公关联系方式',
  customer_service: '客服/工单/反馈渠道',
  group_chat: '群聊/社群',
  other: '其它社工入口',
}

export const WEB_TAGGING_SCOPE_MAP: Record<string, string> = {
  official: '官方公开渠道',
  personal: '私人信息',
  enterprise: '企业级渠道',
}

export const WEB_TAGGING_CHANNEL_MAP: Record<string, string> = {
  email: '邮箱',
  phone: '电话',
  wechat: '微信',
  link: '链接',
  form: '表单',
  other: '其它',
}

export const WEB_TAGGING_ROLE_MAP: Record<string, string> = {
  hr: '招聘/人力',
  business: '商务合作',
  sales: '销售',
  support: '技术支持/售后支持',
  customer_service: '客服/工单/反馈',
  media: '媒体沟通',
  pr: '公关/品牌',
  other: '其它',
}

export const WEB_TAGGING_SUBTYPE_MAP: Record<string, string> = {
  live_chat_third_party: '第三方在线客服/IM',
  live_chat_native: '站内在线客服（自建）',
  ticket_system: '工单系统',
  feedback_form: '反馈表单',
  support_portal: '帮助中心/支持门户',
  hotline_400: '400 热线',
  hotline_landline: '座机热线/总机',
  service_wechat: '客服微信/客服二维码',

  resume_email: '简历投递邮箱',
  resume_phone: '招聘电话',
  job_portal: '招聘入口/招聘系统链接',
  campus_recruit: '校招入口',

  business_email: '商务合作邮箱',
  sales_email: '销售邮箱',
  partner_program: '渠道/生态合作入口',
  supplier_portal: '供应商入口',
  procurement_contact: '采购联系',

  media_email: '媒体邮箱',
  pr_email: '公关邮箱',
  press_contact: '新闻/媒体联系入口',

  mobile_personal: '个人手机号',
  email_personal: '个人邮箱',
  wechat_id_personal: '个人微信号',
  wechat_qr_personal: '个人微信二维码',

  qq_group: 'QQ群',
  wechat_group: '微信群',
  telegram: 'Telegram',
  discord: 'Discord',
  community_invite: '社群邀请链接',
}

export function mapWebTaggingEnum(kind: 'type' | 'scope' | 'channel' | 'role' | 'subtype', value: string | null | undefined): string {
  if (!value) return '-'

  switch (kind) {
    case 'type':
      return WEB_TAGGING_TYPE_MAP[value] || value
    case 'scope':
      return WEB_TAGGING_SCOPE_MAP[value] || value
    case 'channel':
      return WEB_TAGGING_CHANNEL_MAP[value] || value
    case 'role':
      return WEB_TAGGING_ROLE_MAP[value] || value
    case 'subtype':
      return WEB_TAGGING_SUBTYPE_MAP[value] || value
    default:
      return value
  }
}
