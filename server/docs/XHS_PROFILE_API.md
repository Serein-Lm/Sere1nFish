# XHS 人物画像 API 文档

## 接口列表

### 1. 人物画像生成（SSE 流式）

**POST** `/api/v1/xhs/profile/generate/stream`

#### 请求体
```json
{
  "user_url": "https://www.xiaohongshu.com/user/profile/60be48da000000002002c56d",
  "project_id": "6970c09e27b9715e54c7a83e",
  "keyword": "字节跳动 产品经理"
}
```

#### SSE 消息格式

```
data: {"type": "init", "task_id": "abc12345", "user_id": "60be48da000000002002c56d", "stage": "screenshot"}

data: {"type": "status", "message": "📸 开始截屏...", "stage": "screenshot"}

data: {"type": "status", "message": "正在获取 Cookie...", "stage": "screenshot"}

data: {"type": "status", "message": "✅ 截屏完成，共 2 张", "stage": "screenshot"}

data: {"type": "avatar", "avatar_url": "https://sns-avatar-qc.xhscdn.com/avatar/xxx.jpg"}

data: {"type": "status", "message": "🔍 开始视觉分析...", "stage": "vision"}

data: {"type": "content", "content": "## 一、基础信息识别\n\n### 用户昵称\nmomo（找暑期版）"}

data: {"type": "content", "content": "\n\n### 小红书号\n..."}

data: {"type": "status", "message": "✅ 视觉分析完成", "stage": "vision"}

data: {"type": "status", "message": "🤖 开始结构化输出...", "stage": "format"}

data: {"type": "format_content", "content": "{\"nickname\": \"momo（找暑期版）\""}

data: {"type": "format_content", "content": ", \"basic_info\": {...}"}

data: {"type": "status", "message": "✅ 结构化输出完成", "stage": "format"}

data: {"type": "status", "message": "💾 正在保存到数据库...", "stage": "save"}

data: {"type": "status", "message": "✅ 入库完成", "stage": "save"}

data: {"type": "done", "message": "人物画像生成完成", "user_id": "60be48da000000002002c56d"}
```

#### SSE 消息类型

| type | 说明 | 字段 |
|------|------|------|
| `init` | 初始化 | `task_id`, `user_id`, `stage` |
| `status` | 状态更新 | `message`, `stage` |
| `avatar` | 头像链接 | `avatar_url` |
| `content` | 视觉分析内容（流式） | `content` |
| `format_content` | 结构化输出内容（流式） | `content` |
| `done` | 完成 | `message`, `user_id` |
| `cancelled` | 已取消 | `message`, `stage` |
| `error` | 错误 | `message` |

#### stage 阶段

| stage | 说明 | 可取消 |
|-------|------|--------|
| `screenshot` | 截屏阶段 | ✅ |
| `vision` | 视觉分析（API调用中） | ❌ |
| `format` | Agent格式化（API调用中） | ❌ |
| `save` | 入库阶段 | ✅ |

---

### 2. 取消 SSE 任务

**POST** `/api/v1/xhs/sse/cancel/{task_id}`

#### 响应
```json
{
  "success": true,
  "message": "任务 abc12345 已取消",
  "stage": "screenshot",
  "immediate": true
}
```

如果在 API 调用中（vision/format 阶段）：
```json
{
  "success": true,
  "message": "任务已标记取消，但当前处于 vision 阶段，API 调用无法中断，将在完成后停止",
  "stage": "vision",
  "immediate": false
}
```

---

### 3. 获取人物画像列表

**GET** `/api/v1/xhs/{project_id}/profiles?limit=50&skip=0`

#### 响应示例
```json
[
  {
    "id": "69733f185e7981b8b812207e",
    "project_id": "6970c09e27b9715e54c7a83e",
    "task_id": "",
    "user_id": "60be48da000000002002c56d",
    "nickname": "momo（找暑期版）",
    "avatar_url": "https://sns-avatar-qc.xhscdn.com/avatar/1040g2jo31mbvsc2p5m6g5o5u93d85hbdisq48d0?imageView2/2/w/60/format/webp",
    "basic_info": {
      "xhs_id": "123456789",
      "ip_location": "北京",
      "account_type": "求职号",
      "account_age_estimate": "普通号",
      "verification": null
    },
    "stats": {
      "follows": "156",
      "fans": "892",
      "likes_and_collects": "3.2万",
      "notes_count": "45",
      "activity_level": "高",
      "influence_level": "小博主"
    },
    "identity": {
      "company": "字节跳动",
      "industry": "互联网",
      "position": "搜推策略产品实习生",
      "position_level": "实习生",
      "department": "搜索推荐",
      "work_years": "0",
      "employment_status": "实习中",
      "confidence": "高"
    },
    "bio_analysis": {
      "raw": "27届 双非本 211硕 得物-美团-字节 搜推策略产品实习 找搜推策略产品转正ing~",
      "identity_tags": ["27届", "双非本", "211硕", "搜推策略产品"],
      "education": {
        "school": null,
        "school_tier": "211",
        "degree": "硕士",
        "major": null,
        "graduation_year": "2027"
      },
      "location": {
        "city": "北京",
        "district": null,
        "work_address_hint": null,
        "matches_ip": true
      },
      "contact_exposed": {
        "wechat": null,
        "email": null,
        "phone": null,
        "other_social": []
      },
      "interests": ["求职", "互联网", "产品经理"],
      "life_stage": "学生",
      "life_events": ["找实习转正"]
    },
    "device_info": {
      "computer_os": "macOS",
      "computer_brand": "MacBook",
      "phone_brand": "iPhone",
      "evidence": ["截图UI风格为macOS", "笔记中提及MacBook"]
    },
    "avatar_analysis": {
      "type": "卡通",
      "is_real_person": false,
      "gender_from_avatar": "未知",
      "age_estimate": null,
      "appearance_features": [],
      "dress_style": null,
      "has_work_badge": false,
      "badge_info": null,
      "has_company_logo": false,
      "company_logo_info": null,
      "background_location": null,
      "background_clues": [],
      "photo_professionalism": "普通"
    },
    "gender_analysis": {
      "conclusion": "女",
      "confidence": "中",
      "evidence": {
        "from_avatar": "卡通头像，无法判断",
        "from_nickname": "momo 偏中性",
        "from_bio": "无明显性别特征",
        "from_notes": "内容风格偏女性化",
        "from_writing_style": "使用表情符号较多"
      }
    },
    "personality_profile": {
      "keywords": ["积极进取", "目标导向", "分享型", "焦虑", "社交活跃"],
      "mbti_estimate": "ENFJ",
      "big_five": {
        "openness": "高",
        "conscientiousness": "高",
        "extraversion": "高",
        "agreeableness": "中",
        "neuroticism": "中"
      },
      "communication_style": "热情主动，乐于分享经验",
      "content_style": "分享型",
      "emotional_tendency": "积极",
      "values_hint": ["职业发展", "大厂认可"],
      "vulnerability_points": ["求职焦虑", "渴望认可"],
      "trust_building_approach": "以求职经验交流为切入点"
    },
    "notes_analysis": {
      "total_visible": "45",
      "content_distribution": [
        {"category": "求职经验", "count": "20", "percentage": "44%", "social_value": "高"},
        {"category": "实习分享", "count": "15", "percentage": "33%", "social_value": "高"},
        {"category": "生活记录", "count": "10", "percentage": "23%", "social_value": "低"}
      ],
      "posting_pattern": {
        "frequency": "周更",
        "active_time": "晚间",
        "recent_activity": "近一周有更新"
      },
      "sensitive_notes": [
        {
          "title": "字节实习涨薪啦！分享我的薪资",
          "type": "薪资曝光",
          "sensitive_level": "高",
          "exposed_info": ["实习薪资", "涨薪幅度"],
          "exploitability": "可用于验证身份或套取更多信息"
        },
        {
          "title": "内推码来啦！字节跳动2026暑期实习",
          "type": "内推",
          "sensitive_level": "极高",
          "exposed_info": ["内推码", "内推渠道"],
          "exploitability": "可用于伪造身份投递"
        }
      ],
      "work_content": {
        "has_work_content": true,
        "work_topics": ["搜索推荐", "策略产品", "实习体验"],
        "project_mentions": [],
        "tool_mentions": [],
        "insider_level": "轻微"
      },
      "consumption_hints": {
        "spending_level": "中",
        "brand_preferences": ["Apple"],
        "lifestyle_indicators": ["学生党", "北漂"]
      }
    },
    "company_identification": {
      "identified_company": "字节跳动",
      "confidence": "high",
      "evidence": ["简介明确提及字节", "多篇笔记分享字节实习经历", "内推码为字节跳动"],
      "company_type": "互联网大厂",
      "company_scale": "万人以上",
      "industry": "互联网",
      "business_line": "搜索推荐",
      "office_location": "北京",
      "related_companies": ["得物", "美团"],
      "competitor_of": []
    },
    "keyword_relevance": {
      "score": 95,
      "target_company": "字节跳动",
      "target_keyword": "产品经理",
      "relationship": "直接员工",
      "evidence": ["简介明确提及在字节实习", "职位为搜推策略产品", "多篇笔记验证"],
      "analysis": "用户是字节跳动搜索推荐部门的策略产品实习生，与搜索关键词高度匹配"
    },
    "attack_surface": {
      "risk_score": 75,
      "risk_level": "高",
      "identity_confirmation": {
        "confirmed": true,
        "real_name_exposed": false,
        "real_name": null,
        "confidence": "高"
      },
      "exposed_information": [
        {
          "category": "工作",
          "type": "公司信息",
          "value": "字节跳动搜索推荐部门实习生",
          "source": "简介",
          "sensitivity": "中",
          "freshness": "新鲜",
          "exploitability": "可用于定向社工"
        },
        {
          "category": "工作",
          "type": "薪资信息",
          "value": "实习薪资及涨薪情况",
          "source": "笔记",
          "sensitivity": "高",
          "freshness": "新鲜",
          "exploitability": "可用于验证身份"
        },
        {
          "category": "身份",
          "type": "教育背景",
          "value": "27届 双非本 211硕",
          "source": "简介",
          "sensitivity": "中",
          "freshness": "新鲜",
          "exploitability": "可用于筛选目标"
        }
      ],
      "credential_leaks": {
        "internal_codes": ["字节跳动内推码"],
        "system_access": [],
        "account_hints": []
      },
      "attack_vectors": [
        {
          "vector": "内推码伪装",
          "method": "使用其内推码投递简历，建立联系后套取更多信息",
          "prerequisites": ["获取内推码"],
          "difficulty": "低",
          "success_probability": "高",
          "potential_gain": "建立信任关系，获取内部信息"
        },
        {
          "vector": "求职交流",
          "method": "以同为求职者身份接近，交流实习经验",
          "prerequisites": ["了解求职话术"],
          "difficulty": "低",
          "success_probability": "高",
          "potential_gain": "获取团队信息、同事信息"
        }
      ]
    },
    "social_graph": {
      "mentioned_colleagues": [],
      "mentioned_companies": ["得物", "美团", "字节跳动"],
      "team_info": "搜索推荐团队",
      "manager_hints": null,
      "social_circle": "互联网求职圈",
      "relationship_status": "未知",
      "family_info": null
    },
    "timeline": {
      "career_history": [
        {"company": "得物", "position": "实习", "period": "过去", "source": "简介"},
        {"company": "美团", "position": "实习", "period": "过去", "source": "简介"},
        {"company": "字节跳动", "position": "搜推策略产品实习", "period": "当前", "source": "简介"}
      ],
      "education_history": [
        {"school": "双非本科", "degree": "本科", "period": "过去"},
        {"school": "211高校", "degree": "硕士", "period": "当前"}
      ],
      "key_events": ["2024年得物实习", "2025年美团实习", "2025年字节实习", "2026年寻求转正"]
    },
    "profile_summary": "27届硕士研究生，双非本科+211硕士背景，目前在字节跳动搜索推荐部门担任策略产品实习生，正在寻求转正机会。此前有得物、美团实习经历。用户活跃度高，频繁分享求职经验和实习心得，已暴露内推码、薪资信息等敏感内容。性格积极进取，渴望大厂认可，存在求职焦虑心理。可通过求职交流、内推码等方式建立联系，社工价值较高。",
    "attention_score": 82,
    "recommended_actions": [
      {
        "action": "监控笔记更新",
        "description": "持续关注用户笔记，收集更多内部信息",
        "priority": "高",
        "difficulty": "低",
        "expected_outcome": "获取团队动态、项目信息",
        "risk": "低"
      },
      {
        "action": "求职交流接近",
        "description": "以求职者身份私信交流，建立信任",
        "priority": "高",
        "difficulty": "低",
        "expected_outcome": "获取同事信息、内部流程",
        "risk": "低"
      },
      {
        "action": "内推码利用",
        "description": "使用内推码投递，后续以感谢为由深入交流",
        "priority": "中",
        "difficulty": "低",
        "expected_outcome": "建立长期联系",
        "risk": "中"
      }
    ],
    "tags": ["字节跳动", "实习生", "产品经理", "搜索推荐", "27届", "211硕士", "求职中", "高价值目标"],
    "note_ids": [],
    "notes_count": 0,
    "created_at": "2026-01-23T09:27:52.430000",
    "updated_at": "2026-01-23T17:30:00.000000"
  }
]
```

---

### 4. 获取单个人物画像

**GET** `/api/v1/xhs/profiles/{profile_id}`

响应格式同上。

---

### 5. 删除人物画像

**DELETE** `/api/v1/xhs/profiles/{profile_id}`

#### 响应
```json
{
  "ok": true,
  "message": "人物画像已删除"
}
```

#### 错误响应
```json
{
  "detail": "人物画像不存在"
}
```

---

## 字段说明

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | MongoDB ID |
| `project_id` | string | 项目 ID |
| `user_id` | string | 小红书用户 ID |
| `nickname` | string | 用户昵称 |
| `avatar_url` | string | 头像链接 |
| `attention_score` | int | 关注度评分 (0-100) |
| `tags` | array | 标签列表 |
| `profile_summary` | string | 画像描述 |

### 分析结果字段

| 字段 | 说明 |
|------|------|
| `basic_info` | 基础信息（小红书号、IP属地、账号类型） |
| `stats` | 账号数据（关注/粉丝/获赞/笔记数） |
| `identity` | 身份信息（公司/行业/职位/职级） |
| `bio_analysis` | 简介分析（教育/位置/联系方式/兴趣） |
| `device_info` | 设备信息（电脑/手机品牌） |
| `avatar_analysis` | 头像分析 |
| `gender_analysis` | 性别分析 |
| `personality_profile` | 性格画像（MBTI/大五人格/弱点） |
| `notes_analysis` | 笔记分析（分类/敏感笔记/工作内容） |
| `company_identification` | 公司判定 |
| `keyword_relevance` | 关键词关联度 |
| `attack_surface` | 攻击面分析（风险评分/暴露信息/攻击向量） |
| `social_graph` | 社交图谱 |
| `timeline` | 时间线（职业/教育历史） |
| `recommended_actions` | 建议行动 |

---

## 评分说明

### attention_score（关注度）
- 80-100: 高价值目标，建议重点关注
- 60-79: 中等价值，有一定社工潜力
- 40-59: 一般价值，信息有限
- 0-39: 低价值，不建议投入资源

### keyword_relevance.score（关键词关联度）
- 90-100: 确认是目标公司员工
- 70-89: 很可能是目标公司员工
- 50-69: 可能相关
- 0-49: 关联度低或无关

### attack_surface.risk_score（风险评分）
- 80-100: 极高风险，可直接发起攻击
- 60-79: 高风险，有明确攻击路径
- 40-59: 中等风险
- 0-39: 低风险
