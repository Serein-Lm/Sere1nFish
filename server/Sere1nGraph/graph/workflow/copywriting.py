"""
话术生成 — ReAct Agent 驱动

Agent 拥有 skill tools，自主决定加载哪些 skill 和案例。
不再手动拼 prompt，不再硬编码阶段。

调用方式：
    agent = await create_copywriting_agent(app_config)
    result = await agent({"messages": [HumanMessage(content=context)]})
"""

# 本模块仅作为入口说明。
# 实际的 agent 创建在 agents/factory.py 的 create_copywriting_agent()。
# pipeline 直接调用 factory 创建 agent 即可。
