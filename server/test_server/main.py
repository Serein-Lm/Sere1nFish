"""
测试服务入口（独立运行，无需鉴权，仅本地访问）
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from test_server.routers.mock_sse_simple import router as mock_sse_simple_router

app = FastAPI(
    title="Agent 测试服务",
    description="SSE 流式输出测试（无需鉴权，仅本地访问）",
    version="1.0.0",
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(mock_sse_simple_router, prefix="/mock", tags=["Mock SSE（简化版-展示结构）"])


@app.get("/")
async def root():
    return {
        "message": "AI Agent 测试服务",
        "endpoints": {
            "Mock SSE（简化版）": "/mock/stream-simple",
            "健康检查": "/mock/ping-simple",
            "API 文档": "/docs"
        }
    }
