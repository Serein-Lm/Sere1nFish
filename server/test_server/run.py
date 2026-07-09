"""
测试服务启动脚本（热更新 + 仅本地访问）
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import uvicorn

if __name__ == "__main__":
    print("=" * 50)
    print("🧪 Agent 测试服务")
    print("=" * 50)
    print()
    print("🚀 启动服务: http://127.0.0.1:8001")
    print("📄 测试页面: http://127.0.0.1:8001/sse/")
    print("📚 API 文档: http://127.0.0.1:8001/docs")
    print()
    print("⚠️  仅允许本地访问，无需鉴权")
    print("=" * 50)
    print()
    
    uvicorn.run(
        "test_server.main:app",
        host="127.0.0.1",  # 仅本地访问
        port=8001,  # 使用不同端口，避免与主服务冲突
        reload=True,
        reload_dirs=[str(project_root)],
        reload_excludes=["*.pyc", "__pycache__", ".venv", ".git"],
    )
