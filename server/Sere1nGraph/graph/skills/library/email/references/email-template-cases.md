# 邮箱话术实战案例

## 案例 1: 投标方案邮件

```json
{
  "channel": "email",
  "email_template": "发件人: 张明 <zhangming@xx-tech.com>\n主题: 关于XX项目投标技术方案 - XX科技\n\nXX总，您好：\n\n我司已完成贵单位XX项目的技术方案编制，现将方案及报价发送给您，请查阅。\n\n附件已加密，密码为：bid26\n（出于商业保密考虑进行了加密处理）\n\n如有疑问请致电：138-xxxx-xxxx\n\n此致\n敬礼\n\n张明\nXX科技有限公司 | 商务部经理\n电话：138-xxxx-xxxx\n邮箱：zhangming@xx-tech.com",
  "key_points": ["发件人域名要可信", "主题包含项目名", "密码简短好记", "签名完整"]
}
```

## 案例 2: 安全漏洞通知邮件

```json
{
  "channel": "email",
  "email_template": "发件人: 安全响应中心 <security@xx-sec.com>\n主题: 【紧急】贵司系统存在高危漏洞 - 请尽快处理\n\n李工，您好：\n\n我司安全监测平台发现贵公司XX系统存在CVE-2026-XXXX高危漏洞，该漏洞可能导致远程代码执行。\n\n附件为修复工具及操作指南，请尽快部署。\n密码：sec26\n\n如需技术支持请联系：\n张明 | 技术支持工程师\n电话：138-xxxx-xxxx\n\n此致\n\nXX安全科技\n安全响应中心",
  "key_points": ["利用CVE编号增加可信度", "制造紧迫感", "提供技术支持联系方式"]
}
```

## 案例 3: 跟进邮件（目标未回复）

```json
{
  "channel": "email",
  "email_template": "发件人: 张明 <zhangming@xx-tech.com>\n主题: Re: 关于XX项目投标技术方案 - 跟进\n\nXX总，您好：\n\n上封邮件不知您是否收到？方案提交截止日期临近（本周五），烦请您抽空查阅附件中的技术方案。\n\n如有任何问题可随时联系我，也可以加我微信（138-xxxx-xxxx）沟通更方便。\n\n此致\n\n张明\nXX科技有限公司 | 商务部经理",
  "key_points": ["Re: 前缀增加连续性", "制造截止日期紧迫感", "引导到微信渠道"]
}
```
