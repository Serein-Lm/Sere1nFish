# 内网二次钓鱼实战案例

## 案例 1: IT 部门系统升级通知

```json
{
  "channel": "intranet",
  "email_template": "发件人: IT运维中心 <it-ops@internal.corp>\n主题: 【紧急】VPN客户端安全升级通知\n\n各位同事：\n\n近期发现VPN客户端存在安全隐患，请所有员工在今日下班前完成升级。\n\n升级包下载：[内网链接]\n操作指南见附件。\n\nIT运维中心\n2026年3月",
  "key_points": ["模仿内部邮件签名格式", "使用内部邮箱域名", "制造紧迫感（今日下班前）", "工作时间发送"]
}
```

## 案例 2: 领导名义紧急文件

```json
{
  "channel": "intranet",
  "dialogue": [
    {"role": "attacker", "content": "小李，王总让我把这个文件转给你，说你今天下班前处理完", "tactic": "权威效应+紧迫感"},
    {"role": "target", "content": "好的，什么文件？", "tactic": null},
    {"role": "attacker", "content": "[发送文件] Q1审计报告_待确认.zip", "tactic": null},
    {"role": "attacker", "content": "密码 audit1，你看完签字发回来", "tactic": "一致性"}
  ],
  "key_points": ["利用已知的组织架构关系", "引用真实领导姓名", "文件命名符合内部规范"]
}
```
