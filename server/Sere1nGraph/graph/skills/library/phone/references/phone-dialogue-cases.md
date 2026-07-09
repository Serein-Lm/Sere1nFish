# 电话话术实战案例

## 案例 1: 安全厂商技术支持

```json
{
  "channel": "phone",
  "dialogue": [
    {"role": "attacker", "content": "喂，您好，请问是XX公司信息部的李工吗？", "tactic": "确认身份"},
    {"role": "target", "content": "是的，你是？", "tactic": null},
    {"role": "attacker", "content": "李工您好，我是XX安全的技术支持张明，我们监测到贵公司的XX系统存在一个高危漏洞", "tactic": "权威效应+紧迫感"},
    {"role": "target", "content": "什么漏洞？", "tactic": null},
    {"role": "attacker", "content": "是最近公开的CVE-2026-XXXX，影响范围比较大，我们已经出了修复工具", "tactic": "权威效应"},
    {"role": "target", "content": "哦，严重吗？", "tactic": null},
    {"role": "attacker", "content": "挺严重的，可能导致远程代码执行，建议您尽快处理。我可以把修复工具发您邮箱", "tactic": "紧迫感+互惠原则"},
    {"role": "target", "content": "好的，发我邮箱吧", "tactic": null},
    {"role": "attacker", "content": "好的，您邮箱是 li@xx.com 对吧？我马上发，密码是 sec26", "tactic": "确认信息"},
    {"role": "target", "content": "对的，谢谢", "tactic": null}
  ],
  "key_points": ["语气专业冷静", "用CVE编号增加可信度", "主动提供修复方案（互惠）", "自然过渡到发送文件"]
}
```

## 案例 2: 商务跟进电话

```json
{
  "channel": "phone",
  "dialogue": [
    {"role": "attacker", "content": "王总您好，我是XX科技的张明，之前给您发了一封关于XX项目方案的邮件，不知道您收到了没有", "tactic": "社会认同"},
    {"role": "target", "content": "哦，好像看到了，还没来得及看", "tactic": null},
    {"role": "attacker", "content": "理解的，您工作忙。主要是投标截止日快到了，想确认下您看完方案后我们约个时间详细聊", "tactic": "紧迫感"},
    {"role": "target", "content": "好的，我今天看看", "tactic": null},
    {"role": "attacker", "content": "好的，附件密码是 bid26，您看完有什么问题随时微信联系我，我微信号就是手机号", "tactic": "引导加微信"},
    {"role": "target", "content": "行", "tactic": null}
  ],
  "key_points": ["跟进邮件的电话要简短", "制造截止日期紧迫感", "自然引导到微信渠道"]
}
```
