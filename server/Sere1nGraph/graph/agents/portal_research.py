"""Official portal research browser adapter with explicit traversal boundaries."""
from __future__ import annotations
import re
from urllib.parse import urlsplit

from api.services.portal_link_policy import canonical_link, in_roots, relation_hint

PAGE_READ = r"""() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = value => { try { return new URL(value, location.href).href; } catch { return ''; } };
  const root = document.querySelector('article, main, [role="main"], #content, .content') || document.body;
  const context = node => {
    const parts = [];
    for (let p=node.parentElement, i=0; p && p.tagName!=='BODY' && i<4; p=p.parentElement,i++) {
      parts.push(p.id,p.className,p.getAttribute('aria-label'),p.getAttribute('label'));
      for (const child of [...p.children].slice(0,3)) if (/^(H[1-4]|LABEL|LEGEND|STRONG|DT)$/.test(child.tagName)) parts.push(child.textContent.slice(0,100));
    }
    return clean(parts.join(' ')).slice(0,600);
  };
  const links = [...document.querySelectorAll('a[href], option[value]')].map(a => ({
    url: a.href || absolute(a.value || ''), title: clean(a.textContent || a.title).slice(0,160), context: context(a)
  })).filter(a => /^https?:\/\//i.test(a.url));
  const important = /招聘|招标|采购|招商|投资|反馈|留言|联系|业务|服务|产品|关于|简介|下属|直属|上级|主管|子公司|成员|友链|友好|合作伙伴/;
  links.sort((a,b)=>Number(important.test(b.title+' '+b.context))-Number(important.test(a.title+' '+a.context)));
  return {url:location.href,title:clean(document.title).slice(0,300),text:clean(root?.innerText).slice(0,12000),links:links.slice(0,100)};
}"""

class PortalBrowserPolicy:
    def __init__(self, roots, options):
        self.roots = roots
        self.options = options
        self.blocked = set()
        self.blocked_names = set()
        self.blocked_roots = set()
        self.disabled_links = set()
        self.disabled_roots = set()
        self.external = set()
        self.related_roots = set()
        self.visits = {}

    def observe_page(self, page):
        if not in_roots(page.get("url", ""), self.roots): return
        for link in page.get("links") or []:
            url = canonical_link(link.get("url", ""))
            if not url: continue
            hint = relation_hint(str(link.get("title") or ""), str(link.get("context") or ""))
            host = urlsplit(url).hostname or ""
            separate_host = host not in {value for root in self.roots for value in (root, "www." + root)}
            if hint == "friend":
                self.blocked.add(url)
                self.blocked_names.add(str(link.get("title") or ""))
                if separate_host: self.blocked_roots.add(host)
            elif hint == "subordinate" and not self.options.include_subordinates or hint == "parent" and not self.options.include_parent:
                self.disabled_links.add(url)
                if separate_host: self.disabled_roots.add(host)
            elif hint == "subordinate" and self.options.include_subordinates or hint == "parent" and self.options.include_parent:
                self.external.add(url)
                self.related_roots.add(urlsplit(url).hostname or "")
            elif hint == "ordinary" and re.search(r"招聘|招标|采购|招商|反馈|留言|咨询|客服|办事", str(link.get("title") or "")):
                self.external.add(url)

    def guard(self, name, args, kwargs):
        if name not in {"navigate_page", "navigate"}: return None
        payload = args[0] if args and isinstance(args[0], dict) else kwargs
        url = canonical_link(str(payload.get("url") or ""))
        if not url: return "只允许读取公开 HTTP(S) 网页。"
        if url in self.disabled_links or in_roots(url, list(self.disabled_roots)): return "本轮未开启该方向的单位扩展，请继续本单位门户。"
        if url in self.blocked or in_roots(url, list(self.blocked_roots)): return "该链接属于友情链接，禁止跳转；请返回本单位门户栏目。"
        host = urlsplit(url).hostname or ""
        allowed = in_roots(url, self.roots) or in_roots(url, list(self.related_roots)) or url in self.external
        if host in {"bing.com", "www.bing.com", "cn.bing.com"}: allowed = True
        if self.options.include_parent and host.endswith('.gov.cn'): allowed = True
        if not allowed: return "该页面不在已核验官网、官网明确发布的业务入口或所选直属关系范围内；禁止跟随无关站点。"
        if url not in self.visits and len(self.visits) >= self.options.max_pages:
            return "已达到本轮页面预算，请按已有证据输出完整报告并说明未覆盖栏目。"
        if self.visits.get(url, 0) >= 2:
            return "本页已有两次导航，请复用证据账本或读取其他栏目。"
        self.visits[url] = self.visits.get(url, 0) + 1
        return None

    def transform(self, name, args, kwargs):
        from .factory import _standardize_research_browser_call
        args, kwargs = _standardize_research_browser_call(name, args, kwargs)
        if name in {"evaluate_script", "evaluate"}:
            key = "script" if name == "evaluate" else "function"
            value = '(' + PAGE_READ + ')()' if key == "script" else PAGE_READ
            if args and isinstance(args[0], dict): args = ({**args[0], key:value}, *args[1:])
            elif args: args = (value, *args[1:])
            else: kwargs = {**kwargs, key:value}
        elif name == "navigate_page":
            if args and isinstance(args[0], dict): args = ({**args[0], "timeout":30000}, *args[1:])
            else: kwargs = {**kwargs, "timeout":30000}
        return args, kwargs

async def create_portal_research_agent(app_config, *, session, observer):
    from .factory import create_agent_node, create_llm, RequireEvidenceToolMiddleware, ToolCallLimitMiddleware, ModelCallLimitMiddleware, SummarizationMiddleware
    from ..prompts.loader import load_prompt
    options = session.options
    return create_agent_node(
        app_config=app_config, model_workload="collection", builtin_tools=[],
        system_prompt=load_prompt("target_research/portal_research"),
        middleware=[RequireEvidenceToolMiddleware(),
            ToolCallLimitMiddleware(run_limit=options.max_tool_calls, exit_behavior="continue"),
            ModelCallLimitMiddleware(run_limit=options.max_tool_calls + 12, exit_behavior="end"),
            SummarizationMiddleware(model=create_llm(app_config, workload="collection"), trigger=("tokens", options.context_tokens), keep=("messages", 8), trim_tokens_to_summarize=options.context_tokens,
                summary_prompt="把研究历史整理为最多 6000 字的证据账本。逐条保留已读 URL、标题、业务事实、各栏目的覆盖与缺口、关系方向及原文证据、明确禁止的友链；不得把推测当事实。\n{messages}"),
        ],
        mcp_server_name="chrome-devtools", mcp_server_profile="readonly_research",
        mcp_tool_names=("navigate_page", "evaluate_script"), parallel_tool_calls=False,
        mcp_tool_limit=options.max_tool_calls, mcp_tool_timeout=45, mcp_error_limit=3,
        timeout=session.remaining_seconds(), max_attempts=1, output_mode="silent",
        mcp_call_transform=session.browser.transform, mcp_call_guard=session.browser.guard,
        mcp_result_observer=observer,
    )
