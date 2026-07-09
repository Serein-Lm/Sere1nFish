# 供应商/合作方实战案例

## 案例 1: 投标方案 — 完整 ScenarioItem 示例

```json
{
  "scenario_name": "投标技术方案提交",
  "target_background": "目标为某机场集团采购部门负责人，近期发布了广告媒体运营招标公告",
  "scenario_overview": "伪造广告传媒公司身份，以提交投标方案为由接触目标，最终发送含载荷的'标书'",
  "faked_identity": {
    "name": "张明",
    "company": "锐视传媒科技有限公司",
    "company_desc": "专注机场广告媒体运营，与多家机场有合作经验",
    "position": "商务部经理",
    "background": "5年机场广告行业经验，主导过3个机场广告项目投标",
    "personality": "专业严谨，注重细节，商务礼仪到位"
  },
  "logic_chain": [
    {"step": 1, "channel": "email", "action": "发送投标意向书及公司资质", "fallback": null},
    {"step": 2, "channel": "phone", "action": "电话确认邮件收到，了解评标时间", "fallback": "再次发送邮件"},
    {"step": 3, "channel": "email", "action": "发送'完整标书及技术方案'压缩包", "fallback": "微信发送"},
    {"step": 4, "channel": "wechat", "action": "微信跟进确认查看情况", "fallback": "电话跟进"}
  ],
  "risk_notes": "目标可能通过天眼查验证公司信息，需要确保伪造公司名有一定可信度"
}
```

## 案例 2: 报价跟进 — ChannelScript 示例

```json
{
  "channel": "phone",
  "dialogue": [
    {"role": "attacker", "content": "王总您好，我是锐视传媒的张明，之前给您发了我们的投标方案", "tactic": "社会认同"},
    {"role": "target", "content": "哦，我看看", "tactic": null},
    {"role": "attacker", "content": "好的，附件密码是 bid26，里面有技术方案和详细报价", "tactic": "互惠原则"},
    {"role": "target", "content": "行，我看完联系你", "tactic": null},
    {"role": "attacker", "content": "好的，投标截止日是下周五，您看完有什么需要调整的随时说", "tactic": "紧迫感"}
  ],
  "key_points": ["利用真实招标信息", "密码与项目相关", "制造截止日期紧迫感"]
}
```
