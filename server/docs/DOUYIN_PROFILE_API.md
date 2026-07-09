# 抖音人物画像 API 文档

## 概述

抖音人物画像 API 提供流式（SSE）接口，自动完成：
1. 截取用户主页截图
2. 使用视觉模型分析截图
3. 生成结构化人物画像
4. 保存到数据库

## API 端点

### 人物画像生成（SSE 流式）

```
POST /api/v1/douyin/profile/generate/stream
```

#### 请求体

```json
{
  "user_url": "https://www.douyin.com/user/MS4wLjABAAAA...",
  "project_id": "项目ID",
  "keyword": "搜索关键词（可选）"
}
```

#### SSE 消息格式

```
data: {"type": "init", "task_id": "abc123", "sec_uid": "MS4wLjABAAAA...", "stage": "screenshot"}

data: {"type": "status", "message": "📸 开始截屏...", "stage": "screenshot"}

data: {"type": "status", "message": "正在截取第 1 张截图...", "stage": "screenshot"}

data: {"type": "status", "message": "✅ 截屏完成，共 3 张", "stage": "screenshot"}

data: {"type": "status", "message": "🔍 开始视觉分析...", "stage": "vision"}

data: {"type": "content", "content": "{\n  \"nickname\": \"用户昵称\""}

data: {"type": "content", "content": ",\n  \"basic_info\": {...}"}

data: {"type": "status", "message": "✅ 视觉分析完成", "stage": "vision"}

data: {"type": "status", "message": "💾 正在保存到数据库...", "stage": "save"}

data: {"type": "status", "message": "✅ 入库完成", "stage": "save"}

data: {"type": "done", "message": "人物画像生成完成", "sec_uid": "MS4wLjABAAAA...", "profile_id": "..."}
```

#### 消息类型

| type | 说明 |
|------|------|
| `init` | 初始化，包含 task_id 和 sec_uid |
| `status` | 状态更新，包含 message 和 stage |
| `content` | 视觉分析内容片段（流式输出） |
| `done` | 完成，包含 sec_uid 和 profile_id |
| `cancelled` | 任务已取消 |
| `error` | 错误信息 |

#### 阶段（stage）

| stage | 说明 |
|-------|------|
| `screenshot` | 截屏阶段 |
| `vision` | 视觉分析阶段 |
| `save` | 入库阶段 |

### 取消任务

```
POST /api/v1/douyin/sse/cancel/{task_id}
```

#### 响应

```json
{
  "success": true,
  "message": "任务 abc123 已取消"
}
```

## 前端集成示例

### JavaScript (EventSource)

```javascript
const eventSource = new EventSource('/api/v1/douyin/profile/generate/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    user_url: 'https://www.douyin.com/user/MS4wLjABAAAA...',
    project_id: 'xxx',
    keyword: '目标公司'
  })
});

let taskId = null;
let analysisContent = '';

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  switch (data.type) {
    case 'init':
      taskId = data.task_id;
      console.log('任务开始:', taskId);
      break;
    
    case 'status':
      console.log(`[${data.stage}] ${data.message}`);
      break;
    
    case 'content':
      analysisContent += data.content;
      // 实时显示分析内容
      break;
    
    case 'done':
      console.log('完成:', data.profile_id);
      eventSource.close();
      break;
    
    case 'error':
      console.error('错误:', data.message);
      eventSource.close();
      break;
  }
};

// 取消任务
function cancelTask() {
  if (taskId) {
    fetch(`/api/v1/douyin/sse/cancel/${taskId}`, { method: 'POST' });
    eventSource.close();
  }
}
```

### Python (httpx)

```python
import httpx
import json

async def generate_profile():
    async with httpx.AsyncClient() as client:
        async with client.stream(
            'POST',
            'http://localhost:8000/api/v1/douyin/profile/generate/stream',
            json={
                'user_url': 'https://www.douyin.com/user/MS4wLjABAAAA...',
                'project_id': 'xxx',
                'keyword': '目标公司'
            },
            headers={'Authorization': 'Bearer xxx'}
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith('data: '):
                    data = json.loads(line[6:])
                    print(data)
```

## 人物画像数据结构

生成的人物画像包含以下字段：

```json
{
  "nickname": "用户昵称",
  "basic_info": {
    "douyin_id": "抖音号",
    "ip_location": "IP属地",
    "account_type": "个人号/营销号/企业号",
    "gender": "男/女/未知"
  },
  "stats": {
    "follows": "关注数",
    "fans": "粉丝数",
    "interaction": "获赞数",
    "videos_count": "作品数"
  },
  "identity": {
    "company": "公司名称",
    "industry": "所属行业",
    "position": "职位",
    "department": "部门"
  },
  "company_identification": {
    "identified_company": "判定的公司",
    "confidence": "high/medium/low",
    "evidence": ["判断依据"]
  },
  "keyword_relevance": {
    "score": 0-100,
    "relationship": "直接员工/前员工/无关"
  },
  "attack_surface": {
    "risk_score": 0-100,
    "exposed_information": [...]
  },
  "profile_summary": "综合人物画像描述",
  "attention_score": 0-100,
  "tags": ["标签1", "标签2", ...]
}
```

## 注意事项

1. 需要先激活一个有效的抖音 Cookie
2. 截图过程需要 30-60 秒（包含页面加载等待）
3. 视觉分析使用配置的 vision_model（默认 gpt-4o）
4. 建议在前端显示进度状态，提升用户体验
