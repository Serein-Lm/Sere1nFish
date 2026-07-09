"""
测试: 纯 LLM Agent（打标/画像/话术）不报 ToolNode 错误

用法:
    python test_server/tests/test_pure_llm_agents.py
"""
import asyncio
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


async def main():
    from Sere1nGraph.graph.config.loader import load_config
    from Sere1nGraph.graph.agents.factory import (
        create_xhs_note_tagging_agent,
        create_xhs_detail_tagging_agent,
        create_xhs_profile_agent,
        create_copywriting_agent,
    )
    from langchain_core.messages import HumanMessage

    config_path = str(_root / "config.json")
    app_config = load_config(config_path)

    tests = [
        ("笔记打标 Agent", create_xhs_note_tagging_agent, "搜索关键词: 百度 实习\n请对以下小红书笔记进行社工攻击面分析:\n标题: 百度实习生的一天\n简介: 分享一下在百度实习的日常\n用户昵称: 小明\n点赞数: 100"),
        ("详情打标 Agent", create_xhs_detail_tagging_agent, "请对以下小红书笔记详情进行深度分析:\n标题: 百度实习生的一天\n完整内容:\n今天是在百度实习的第30天，坐标北京海淀区西二旗，每天早上9点到公司..."),
        ("话术生成 Agent", create_copywriting_agent, "# 目标公司信息\n- 公司名称: 百度\n- 行业: 互联网\n\n# 目标人物画像\n- 昵称: 小明\n- 职位: 实习生\n\n# 任务要求\n请生成1套社工话术。输出JSON格式。"),
    ]

    for name, factory, prompt in tests:
        print(f"\n{'='*50}")
        print(f"测试: {name}")
        print(f"{'='*50}")

        t0 = time.time()
        try:
            agent = await factory(app_config)
            result = await agent({"messages": [HumanMessage(content=prompt)]})
            elapsed = time.time() - t0

            messages = result.get("messages", [])
            last_content = ""
            for msg in reversed(messages):
                c = getattr(msg, "content", "")
                if isinstance(c, str) and c.strip():
                    last_content = c
                    break

            print(f"  ✅ 成功 ({elapsed:.1f}s)")
            print(f"  输出前150字: {last_content[:150]}")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  ❌ 失败 ({elapsed:.1f}s): {e}")


if __name__ == "__main__":
    asyncio.run(main())
