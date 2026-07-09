# 招聘场景实战案例

## 案例 1: 猎头推荐 — 完整 ScenarioItem 示例

```json
{
  "scenario_name": "猎头推荐高薪岗位",
  "target_background": "目标为互联网公司产品经理，在小红书发布求职相关笔记，暴露正在看机会",
  "scenario_overview": "伪造猎头身份，利用目标求职心理，通过推荐高薪岗位建立信任，最终发送含载荷的'JD资料包'",
  "faked_identity": {
    "name": "张明",
    "company": "锐才猎头",
    "company_desc": "专注互联网中高端人才猎聘，与字节、阿里等有合作",
    "position": "高级猎头顾问",
    "background": "5年互联网猎头经验，专注产品和技术岗位",
    "personality": "热情专业，善于倾听，有亲和力"
  },
  "logic_chain": [
    {"step": 1, "channel": "wechat", "action": "通过Boss直聘/脉脉添加目标微信", "fallback": "直接在招聘平台私信"},
    {"step": 2, "channel": "wechat", "action": "微信沟通岗位详情，建立信任", "fallback": null},
    {"step": 3, "channel": "wechat", "action": "发送'JD及公司介绍'压缩包", "fallback": "改为邮件发送"},
    {"step": 4, "channel": "phone", "action": "电话跟进确认是否查看", "fallback": "微信追问"}
  ],
  "risk_notes": "目标可能在招聘平台验证猎头身份，需要提前准备可信的招聘平台账号"
}
```

## 案例 2: Offer 发放 — PayloadSpec 示例

```json
{
  "archive_name": "XX科技_Offer_张三_入职材料.zip",
  "exe_name": "入职须知及材料清单.pdf.exe",
  "icon_disguise": "PDF图标",
  "compression_method": "zip_double",
  "password": "offer1",
  "notes": "第一层文件夹包含真实的入职须知PDF和伪装的exe"
}
```
