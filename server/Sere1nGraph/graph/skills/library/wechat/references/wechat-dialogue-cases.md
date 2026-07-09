# 微信话术实战案例

## 案例 1: 猎头推荐（招聘场景）

```json
{
  "channel": "wechat",
  "dialogue": [
    {"role": "attacker", "content": "您好，我是XX猎头的顾问小张，在Boss上看到您的简历，有个不错的机会想和您聊聊", "tactic": "互惠原则"},
    {"role": "target", "content": "什么机会？", "tactic": null},
    {"role": "attacker", "content": "是XX公司的高级产品经理岗位，年薪50-80w，您的背景非常匹配", "tactic": "稀缺性+虚荣心"},
    {"role": "target", "content": "可以了解下", "tactic": null},
    {"role": "attacker", "content": "好的，我把JD和公司介绍发您，您看下是否感兴趣", "tactic": "互惠原则"},
    {"role": "attacker", "content": "[发送文件] XX公司_高级产品经理_JD及公司介绍.zip", "tactic": null},
    {"role": "attacker", "content": "密码是 jd2026，里面有详细的岗位说明和薪资结构", "tactic": "权威效应"},
    {"role": "target", "content": "好的我看看", "tactic": null},
    {"role": "attacker", "content": "您看完有什么想法随时跟我说，这个HC比较紧急，下周就截止了", "tactic": "紧迫感"},
    {"role": "target", "content": "好", "tactic": null}
  ],
  "key_points": ["好友验证消息: XX猎头顾问-张明-推荐高薪岗位", "备注名: 张明-XX猎头"]
}
```

## 案例 2: 供应商对接（商务场景）

```json
{
  "channel": "wechat",
  "dialogue": [
    {"role": "attacker", "content": "李总您好，我是XX科技的张明，之前和您公司王经理沟通过XX项目的事情", "tactic": "权威效应+社会认同"},
    {"role": "target", "content": "哦，什么项目？", "tactic": null},
    {"role": "attacker", "content": "就是贵公司招标的那个XX系统，我们方案已经做好了，王经理让我直接发给您", "tactic": "权威效应"},
    {"role": "target", "content": "好的你发吧", "tactic": null},
    {"role": "attacker", "content": "方案比较详细有好几个文件，我打包发您", "tactic": "合理化"},
    {"role": "attacker", "content": "[发送文件] XX项目_技术方案_v2.1.zip", "tactic": null},
    {"role": "attacker", "content": "密码是 bid26，出于商业保密加了密", "tactic": "合理化+权威效应"},
    {"role": "target", "content": "收到", "tactic": null},
    {"role": "attacker", "content": "您看完方案我们约个时间详细聊下，投标截止日快到了", "tactic": "紧迫感"},
    {"role": "target", "content": "好的", "tactic": null}
  ],
  "key_points": ["好友验证消息: XX科技张明-XX项目技术方案", "备注名: 张明-XX科技商务"]
}
```

## 案例 3: 质疑应对（目标犹豫）

```json
{
  "channel": "wechat",
  "dialogue": [
    {"role": "target", "content": "这个压缩包我打不开啊", "tactic": null},
    {"role": "attacker", "content": "密码是 bid26，您试试。如果还是不行我单独发文件给您", "tactic": "耐心+备选方案"},
    {"role": "target", "content": "为什么要加密？", "tactic": null},
    {"role": "attacker", "content": "因为里面有报价信息，我们公司规定商务文件必须加密传输，您理解下", "tactic": "合理化+权威效应"},
    {"role": "target", "content": "好吧", "tactic": null}
  ],
  "key_points": ["遇到质疑保持耐心", "始终提供备选方案", "用公司规定作为加密理由"]
}
```

## 案例 4: 从在线客服切换到微信（渠道切换场景）

场景：攻击者先通过在线客服建立联系，然后自然切换到微信继续沟通。

```json
{
  "channel": "wechat",
  "scenario": "在线客服→微信渠道切换",
  "dialogue": [
    {"role": "attacker", "content": "李工你好，我是刚才在你们官网客服咨询API问题的王明", "tactic": "承诺一致性（延续之前的对话）"},
    {"role": "target", "content": "哦你好王总，客服那边跟我说了", "tactic": null},
    {"role": "attacker", "content": "嗯，客服系统发文件不太方便，所以加您微信直接沟通", "tactic": "合理化渠道切换"},
    {"role": "target", "content": "好的，您说的那个500错误我看了下，应该是参数格式的问题", "tactic": null},
    {"role": "attacker", "content": "对对对，我们也怀疑是这个原因。我把错误日志和调用代码整理了一下，发您看看", "tactic": "互惠原则（对方已经在帮忙了）"},
    {"role": "target", "content": "好的发吧", "tactic": null},
    {"role": "attacker", "content": "[发送文件] API调用错误日志_XX公司.zip", "tactic": null},
    {"role": "attacker", "content": "密码 api500，里面有我们的API密钥配置所以加了密", "tactic": "合理化"},
    {"role": "target", "content": "收到，我看看", "tactic": null},
    {"role": "attacker", "content": "麻烦了，这个问题卡了我们两天了，比较急", "tactic": "紧迫感+互惠"},
    {"role": "target", "content": "好的我尽快看", "tactic": null}
  ],
  "key_points": [
    "从客服切换到微信时，必须引用之前的对话内容",
    "渠道切换理由要自然（客服系统不方便发文件）",
    "微信对话的语气比客服对话更随意",
    "利用对方已经投入的时间和精力（承诺一致性）"
  ]
}
```
