# IT 支持场景实战案例

## 案例 1: 安全漏洞通知 — 完整 ScenarioItem 示例

```json
{
  "scenario_name": "高危漏洞修复通知",
  "target_background": "目标为某公司信息安全部运维工程师，负责内网系统维护",
  "scenario_overview": "伪造安全厂商身份，利用最新CVE漏洞制造紧迫感，引导目标运行'修复工具'",
  "faked_identity": {
    "name": "张明",
    "company": "锐盾安全科技",
    "company_desc": "专注企业安全防护，为500+企业提供安全服务",
    "position": "技术支持工程师",
    "background": "3年安全行业经验，专注漏洞响应和应急处置",
    "personality": "专业冷静，技术功底扎实，有紧迫感但不慌张"
  },
  "logic_chain": [
    {"step": 1, "channel": "email", "action": "发送安全漏洞通知邮件，附带CVE编号和影响说明", "fallback": null},
    {"step": 2, "channel": "phone", "action": "电话确认邮件收到，强调漏洞严重性", "fallback": "再次发送邮件"},
    {"step": 3, "channel": "email", "action": "发送'修复工具'压缩包", "fallback": "微信发送"},
    {"step": 4, "channel": "phone", "action": "电话指导运行修复工具", "fallback": null}
  ],
  "risk_notes": "运维人员可能会先在沙箱环境测试，需要确保载荷能绕过基础检测"
}
```

## 案例 2: PayloadSpec 示例

```json
{
  "archive_name": "CVE-2026-XXXX_修复工具_v1.2.zip",
  "exe_name": "patch_installer.exe",
  "icon_disguise": "Windows安装程序图标",
  "compression_method": "zip_double",
  "password": "sec26",
  "notes": "第一层包含README.txt（真实的漏洞说明）和伪装的安装程序"
}
```
