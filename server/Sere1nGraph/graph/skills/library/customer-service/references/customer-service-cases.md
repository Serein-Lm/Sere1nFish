# 在线客服话术实战案例

## 案例 1: 技术产品客户咨询（互惠陷阱范式）

场景：目标是一家 SaaS 公司，官网有在线客服入口。攻击者伪装为企业客户，通过技术问题建立信任后投递载荷。

```json
{
  "channel": "wechat",
  "scenario": "技术产品客户咨询",
  "psychology_paradigm": "互惠陷阱",
  "dialogue": [
    {"role": "attacker", "content": "你好，我们公司最近在用你们的XX产品，遇到了一个问题想咨询一下", "tactic": "社会认同（建立客户身份）"},
    {"role": "target", "content": "您好，请问遇到了什么问题呢？", "tactic": null},
    {"role": "attacker", "content": "就是我们在对接API的时候，调用XX接口一直返回500错误", "tactic": "具体化（用技术细节增加可信度）"},
    {"role": "target", "content": "好的，请问您用的是哪个版本？能提供一下错误日志吗？", "tactic": null},
    {"role": "attacker", "content": "v3.2.1，错误日志我截了图，但是这个聊天窗口好像发不了图片？", "tactic": "试探文件传输能力"},
    {"role": "target", "content": "可以发的，您直接拖拽或者点击发送文件按钮就行", "tactic": null},
    {"role": "attacker", "content": "哦好的我试试", "tactic": null},
    {"role": "attacker", "content": "不行诶，一直上传失败，可能是文件太大了。日志文件有好几个", "tactic": "制造渠道切换的合理理由"},
    {"role": "target", "content": "那您可以把日志打包发到我们技术支持邮箱 support@xxx.com", "tactic": null},
    {"role": "attacker", "content": "好的，邮箱是 support@xxx.com 对吧？我这就发", "tactic": "确认信息"},
    {"role": "target", "content": "对的，发完跟我说一声，我让技术同事看一下", "tactic": null},
    {"role": "attacker", "content": "太感谢了！对了怎么称呼您？方便的话加个微信，后续有问题直接找您沟通效率高一些", "tactic": "互惠原则（客服已经帮了忙，不好意思拒绝）"},
    {"role": "target", "content": "我姓李，微信的话我们一般不加客户的，您有问题随时在这里找我就行", "tactic": null},
    {"role": "attacker", "content": "好的李工，那我先把日志发邮件过去，主题写"API调用500错误-XX公司"，里面有错误日志和我们的调用代码，麻烦帮忙看看", "tactic": "合理化（邮件内容与对话一致）"},
    {"role": "target", "content": "好的没问题，收到后我转给技术组", "tactic": null},
    {"role": "attacker", "content": "谢谢！日志文件我加了密，密码是 api500，因为里面有我们的配置信息", "tactic": "合理化压缩包加密"}
  ],
  "key_points": [
    "通过技术问题建立'真实客户'身份",
    "利用客服系统文件传输限制，自然切换到邮件渠道",
    "客服已经投入时间帮忙，产生互惠心理",
    "邮件内容与对话完全一致，不会引起怀疑",
    "加密理由合理（保护配置信息）"
  ],
  "email_template": "发件人: 王明 <wangming@fakecompany.com>\n主题: API调用500错误-XX公司\n\n李工您好：\n\n刚才在线客服跟您沟通过，我们在对接贵司XX产品API时遇到持续的500错误。\n\n附件是错误日志和我们的调用代码，打包在一起了。\n解压密码：api500（因为包含我们的API密钥配置，所以加了密）\n\n麻烦帮忙转给技术同事看一下，比较急，影响到我们的上线计划了。\n\n谢谢！\n\n王明\nXX科技 | 技术部\n电话：138xxxx1234"
}
```

## 案例 2: 投诉升级（损失厌恶 + 情绪施压范式）

场景：目标是一家电商/服务公司，攻击者伪装为不满客户，通过投诉升级获取内部联系方式后投递载荷。

```json
{
  "channel": "wechat",
  "scenario": "客户投诉升级",
  "psychology_paradigm": "损失厌恶 + 情绪施压",
  "dialogue": [
    {"role": "attacker", "content": "你好，我要投诉", "tactic": "情绪施压（开门见山）"},
    {"role": "target", "content": "您好，请问遇到了什么问题？", "tactic": null},
    {"role": "attacker", "content": "我上个月买的XX产品出了严重质量问题，联系了好几次都没解决", "tactic": "损失厌恶（强调已经造成的损失）"},
    {"role": "target", "content": "非常抱歉给您带来不好的体验，请问您的订单号是多少？", "tactic": null},
    {"role": "attacker", "content": "订单号我一会儿找，先说问题。产品用了不到一个月就出故障了，我拍了视频和照片作为证据", "tactic": "转移话题 + 为后续发文件做铺垫"},
    {"role": "target", "content": "好的，您方便描述一下具体是什么故障吗？", "tactic": null},
    {"role": "attacker", "content": "XX部件直接断裂了，差点伤到人。我已经拍了照片和视频，还有购买凭证，这些证据我都保留着", "tactic": "紧迫感（安全问题）+ 暗示有证据文件"},
    {"role": "target", "content": "这个情况确实比较严重，我帮您记录一下，会尽快安排处理", "tactic": null},
    {"role": "attacker", "content": "之前也是这么说的，结果一直没人管。我现在要求你们给个明确的处理方案，不然我只能走消费者投诉渠道了", "tactic": "损失厌恶（威胁升级）"},
    {"role": "target", "content": "理解您的心情，我这边会升级处理。您可以先把照片和视频发过来，我转给售后主管", "tactic": null},
    {"role": "attacker", "content": "好，但是视频文件比较大，这个聊天窗口能发吗？", "tactic": "试探文件传输"},
    {"role": "target", "content": "视频的话建议您发到我们的售后邮箱 aftersale@xxx.com", "tactic": null},
    {"role": "attacker", "content": "行，我把所有证据打包发过去。售后主管叫什么？我邮件里写清楚转给谁", "tactic": "套取内部人员信息"},
    {"role": "target", "content": "您直接发到邮箱就行，我会转给负责人的", "tactic": null},
    {"role": "attacker", "content": "好吧，那我现在就发，主题写"产品质量投诉-证据材料"，里面有照片视频和购买凭证，文件比较多我打包了，密码是 ts2026", "tactic": "合理化"},
    {"role": "target", "content": "好的收到，我会跟进处理的", "tactic": null}
  ],
  "key_points": [
    "投诉场景天然具有情绪压力，客服会更积极配合",
    "安全问题（差点伤到人）会触发客服的紧急处理流程",
    "威胁走投诉渠道制造损失厌恶",
    "证据材料打包发送是完全合理的",
    "可以尝试套取内部人员信息（售后主管姓名）"
  ]
}
```

## 案例 3: 商务咨询（承诺一致性范式）

场景：目标是一家 B2B 公司，攻击者伪装为潜在大客户，通过商务咨询建立关系后投递载荷。

```json
{
  "channel": "wechat",
  "scenario": "潜在客户商务咨询",
  "psychology_paradigm": "承诺一致性",
  "dialogue": [
    {"role": "attacker", "content": "你好，我想了解一下你们的XX解决方案", "tactic": "建立潜在客户身份"},
    {"role": "target", "content": "您好！请问您是哪家公司的？主要想了解哪方面？", "tactic": null},
    {"role": "attacker", "content": "我是XX集团采购部的，我们在做今年的XX项目选型，看到你们的产品想详细了解一下", "tactic": "权威效应（大公司采购）"},
    {"role": "target", "content": "好的！XX集团是吧，我们之前也有服务过类似规模的企业。请问您主要关注哪些功能？", "tactic": null},
    {"role": "attacker", "content": "主要是XX和XX功能，预算大概在XX万左右。能安排个商务对接吗？", "tactic": "承诺一致性（给出预算=表达诚意）"},
    {"role": "target", "content": "当然可以！我帮您对接我们的商务经理。方便留个联系方式吗？", "tactic": null},
    {"role": "attacker", "content": "可以，我微信号是 xxx，或者你们商务经理直接加我也行", "tactic": "获取商务人员联系方式"},
    {"role": "target", "content": "好的，我让商务经理尽快联系您。请问怎么称呼？", "tactic": null},
    {"role": "attacker", "content": "我姓王，王明。对了，我们这边有个初步的需求文档，能发给你们商务看看吗？这样沟通效率高一些", "tactic": "互惠原则（主动提供需求文档）"},
    {"role": "target", "content": "当然可以，您可以发到 sales@xxx.com，或者等商务加您微信后直接发给他", "tactic": null},
    {"role": "attacker", "content": "好的，那我先发邮件过去，主题写"XX集团-XX项目需求文档"，里面有我们的需求说明和技术要求，加了密码保护，密码是 xq2026", "tactic": "合理化（商业文件加密正常）"},
    {"role": "target", "content": "好的王总，收到后我转给商务经理", "tactic": null}
  ],
  "key_points": [
    "大客户身份让客服高度重视，不会轻易拒绝",
    "给出具体预算数字增加可信度",
    "主动提供需求文档是互惠行为，对方不好意思不看",
    "商业文件加密是行业惯例，不会引起怀疑",
    "同时获取了商务人员的联系方式，可以作为后续攻击入口"
  ]
}
```
