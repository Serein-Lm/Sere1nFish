import asyncio
from datetime import datetime

from api.services.info_collection import (
    ProfileRequest,
    ProfileResult,
    SearchRequest,
    SearchResult,
    TagResult,
)
from api.services.info_collection.douyin_tools import DouyinProfileTool, DouyinSearchTool


class _FakeDB:
    pass


def test_douyin_search_tool_uses_cookie_crawler_and_persists(monkeypatch):
    async def _run():
        from api.dao import douyin as douyin_dao

        calls = {"valid": [], "stored": []}

        async def fake_get_active_cookie(db):
            return {"account_name": "account-1", "cookie_string": "cookie-1"}

        async def fake_set_cookie_valid(db, account_name, is_valid):
            calls["valid"].append({"account_name": account_name, "is_valid": is_valid})

        async def fake_create_search_results_batch(db, project_id, keyword, items):
            calls["stored"].append({
                "project_id": project_id,
                "keyword": keyword,
                "items": list(items),
            })

        class _LoginResult:
            success = True
            message = ""

        class _SearchResult:
            success = True
            message = ""
            items = [{
                "aweme_id": "aweme-1",
                "sec_uid": "sec-1",
                "create_time": 1767225600,
                "title": "目标公司实习",
            }]

        class _Crawler:
            def __init__(self):
                self.closed = False
                self.searches = []

            async def login_by_cookie_string(self, cookie_string):
                assert cookie_string == "cookie-1"
                return _LoginResult()

            async def search_videos(self, keyword, count, publish_time):
                self.searches.append({
                    "keyword": keyword,
                    "count": count,
                    "publish_time": publish_time,
                })
                return _SearchResult()

            async def close(self):
                self.closed = True

        crawler = _Crawler()
        monkeypatch.setattr(douyin_dao, "get_active_cookie", fake_get_active_cookie)
        monkeypatch.setattr(douyin_dao, "set_cookie_valid", fake_set_cookie_valid)
        monkeypatch.setattr(douyin_dao, "create_search_results_batch", fake_create_search_results_batch)

        tool = DouyinSearchTool(db=_FakeDB(), crawler=crawler)
        result = await tool.search(
            SearchRequest(
                source="douyin",
                query="目标公司",
                project_id="project-1",
                task_id="task-1",
                limit=10,
                options={"publish_time": 7},
            )
        )

        assert result.count == 1
        assert result.items[0]["user_profile_url"] == "https://www.douyin.com/user/sec-1"
        assert result.items[0]["create_time_str"] == datetime.fromtimestamp(1767225600).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        assert calls["valid"] == [{"account_name": "account-1", "is_valid": True}]
        assert calls["stored"][0]["project_id"] == "project-1"
        assert calls["stored"][0]["keyword"] == "目标公司"
        assert crawler.searches == [{"keyword": "目标公司", "count": 10, "publish_time": 7}]

    asyncio.run(_run())


def test_douyin_stage_search_wrapper_uses_search_tool(monkeypatch):
    async def _run():
        from api.services import douyin_pipeline as douyin_pipeline_module
        from api.services.info_collection import douyin_tools as douyin_tools_module

        calls = {"init": [], "requests": []}

        class _SearchTool:
            name = "douyin_search"

            def __init__(self, **kwargs) -> None:
                calls["init"].append(kwargs)

            async def search(self, request):
                calls["requests"].append(request)
                return SearchResult(
                    source="douyin",
                    query=request.query,
                    items=[{"aweme_id": "aweme-1"}],
                )

        monkeypatch.setattr(douyin_tools_module, "DouyinSearchTool", _SearchTool)

        pipeline = douyin_pipeline_module.DouyinPipeline(_FakeDB(), object())
        result = await pipeline._stage_search(
            project_id="project-1",
            keyword="目标公司",
            max_videos=10,
            publish_time=7,
        )

        assert result == [{"aweme_id": "aweme-1"}]
        assert calls["init"][0]["db"] is pipeline.db
        assert calls["init"][0]["crawler_factory"] == pipeline._get_crawler
        req = calls["requests"][0]
        assert req.source == "douyin"
        assert req.query == "目标公司"
        assert req.project_id == "project-1"
        assert req.task_id == ""
        assert req.limit == 10
        assert req.options["publish_time"] == 7

    asyncio.run(_run())


def test_douyin_stage_tagging_wrapper_runs_streaming_tool(monkeypatch):
    async def _run():
        from api.dao import douyin as douyin_dao
        from api.services.douyin_pipeline import DouyinPipeline
        from api.services.info_collection.factory import InfoCollectionToolFactory

        stored = []
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def fake_create_tagged_result(db, project_id, item):
            stored.append({"project_id": project_id, **dict(item)})

        class _Tool:
            name = "douyin_video_tagging"

            async def tag(self, request):
                nonlocal active, max_active
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                async with lock:
                    active -= 1
                item = dict(request.item)
                item.update({
                    "tag": "potential_employee",
                    "keyword": request.context["keyword"],
                    "priority": 8,
                })
                return TagResult(
                    source="douyin",
                    kind="video",
                    item_id=request.item_id,
                    tagging=item,
                )

        async def fake_create_douyin_video_tagging_tool(self, pipeline_owner):
            return _Tool()

        async def fail_old_agent(self):
            raise AssertionError("旧抖音批量打标不应直接初始化 agent")

        async def fail_old_batch_insert(db, project_id, items):
            raise AssertionError("旧批量打标落库入口不应被调用")

        monkeypatch.setattr(douyin_dao, "create_tagged_result", fake_create_tagged_result)
        monkeypatch.setattr(douyin_dao, "create_tagged_results_batch", fail_old_batch_insert)
        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_douyin_video_tagging_tool",
            fake_create_douyin_video_tagging_tool,
        )
        monkeypatch.setattr(DouyinPipeline, "_get_tagging_agent", fail_old_agent)

        result = await DouyinPipeline(_FakeDB(), object())._stage_tagging(
            project_id="project-1",
            keyword="目标公司",
            videos=[
                {"aweme_id": "aweme-1", "sec_uid": "sec-1"},
                {"aweme_id": "aweme-2", "sec_uid": "sec-2"},
                {"aweme_id": "aweme-3", "sec_uid": "sec-3"},
            ],
        )

        assert sorted(item["aweme_id"] for item in result) == ["aweme-1", "aweme-2", "aweme-3"]
        assert {item["keyword"] for item in result} == {"目标公司"}
        assert sorted(item["aweme_id"] for item in stored) == ["aweme-1", "aweme-2", "aweme-3"]
        assert {item["project_id"] for item in stored} == {"project-1"}
        assert max_active > 1

    asyncio.run(_run())


def test_douyin_pipeline_run_pipeline_uses_streaming_toolset(monkeypatch):
    async def _run():
        from api.dao import douyin as douyin_dao
        from api.services import douyin_pipeline as douyin_pipeline_module
        from api.services.douyin_pipeline import DouyinPipeline
        from api.services.info_collection.factory import InfoCollectionToolFactory

        stored = {"tagged": [], "closed": False}

        async def fake_create_tagged_result(db, project_id, item):
            stored["tagged"].append({"project_id": project_id, **dict(item)})

        async def fake_get_potential_users(db, project_id):
            return [
                {
                    "_id": item["sec_uid"],
                    "nickname": item["nickname"],
                    "user_profile_url": item["user_profile_url"],
                }
                for item in stored["tagged"]
                if item.get("tag") == "potential_employee"
            ]

        monkeypatch.setattr(douyin_dao, "create_tagged_result", fake_create_tagged_result)
        monkeypatch.setattr(douyin_dao, "get_potential_users", fake_get_potential_users)
        monkeypatch.setattr(douyin_pipeline_module, "obs_log", lambda *args, **kwargs: None)

        class _SearchTool:
            name = "douyin_search"

            async def search(self, request):
                assert request.options["publish_time"] == 7
                return SearchResult(
                    source="douyin",
                    query=request.query,
                    items=[
                        {
                            "aweme_id": "aweme-1",
                            "sec_uid": "sec-1",
                            "nickname": "员工A",
                            "user_profile_url": "https://www.douyin.com/user/sec-1",
                        },
                        {
                            "aweme_id": "aweme-2",
                            "sec_uid": "sec-2",
                            "nickname": "账号B",
                            "user_profile_url": "https://www.douyin.com/user/sec-2",
                        },
                    ],
                )

        class _TaggingTool:
            name = "douyin_video_tagging"

            async def tag(self, request):
                item = dict(request.item)
                item["keyword"] = request.context["keyword"]
                if item["aweme_id"] == "aweme-1":
                    item.update({"tag": "potential_employee", "priority": 9})
                else:
                    item.update({"tag": "marketing", "priority": 2})
                return TagResult(
                    source="douyin",
                    kind="video",
                    item_id=item["aweme_id"],
                    tagging=item,
                )

        class _Toolset:
            def state(self):
                return {
                    "douyin_search_tool": _SearchTool(),
                    "douyin_video_tagging_tool": _TaggingTool(),
                }

            async def close(self):
                stored["closed"] = True

        async def fake_create_douyin_toolset(self, pipeline_owner):
            return _Toolset()

        async def fail_old_search(self, *args, **kwargs):
            raise AssertionError("旧抖音搜索阶段不应被 run_pipeline 调用")

        async def fail_old_tagging(self, *args, **kwargs):
            raise AssertionError("旧抖音批量打标阶段不应被 run_pipeline 调用")

        monkeypatch.setattr(InfoCollectionToolFactory, "create_douyin_toolset", fake_create_douyin_toolset)
        monkeypatch.setattr(DouyinPipeline, "_stage_search", fail_old_search)
        monkeypatch.setattr(DouyinPipeline, "_stage_tagging", fail_old_tagging)

        result = await DouyinPipeline(_FakeDB(), object()).run_pipeline(
            project_id="project-1",
            keyword="目标公司",
            max_videos=10,
            publish_time=7,
            enable_profile=False,
            task_id="task-1",
        )

        assert result["videos_count"] == 2
        assert result["potential_count"] == 1
        assert result["profiles_count"] == 0
        assert sorted(item["aweme_id"] for item in stored["tagged"]) == ["aweme-1", "aweme-2"]
        assert {item["tag"] for item in stored["tagged"]} == {"potential_employee", "marketing"}
        assert stored["closed"] is True

    asyncio.run(_run())


def test_douyin_profile_tool_delegates_to_pipeline_owner():
    async def _run():
        calls = []

        class _Owner:
            async def _generate_profile_for_user(self, *, project_id, keyword, user):
                calls.append({
                    "project_id": project_id,
                    "keyword": keyword,
                    "user": dict(user),
                })
                return {"sec_uid": user["_id"], "nickname": user["nickname"]}

        tool = DouyinProfileTool(_Owner())
        result = await tool.generate_profile(
            ProfileRequest(
                source="douyin",
                project_id="project-1",
                task_id="task-1",
                keyword="目标公司",
                options={"user": {"_id": "sec-1", "nickname": "员工A"}},
            )
        )

        assert result.count == 1
        assert result.profiles == [{"sec_uid": "sec-1", "nickname": "员工A"}]
        assert result.meta["sec_uid"] == "sec-1"
        assert calls == [{
            "project_id": "project-1",
            "keyword": "目标公司",
            "user": {"_id": "sec-1", "nickname": "员工A"},
        }]

    asyncio.run(_run())


def test_douyin_profile_stream_runs_profile_tool_concurrently():
    async def _run():
        from api.services.douyin_pipeline import DouyinPipeline

        active = 0
        max_active = 0
        calls = []
        lock = asyncio.Lock()

        class _ProfileTool:
            name = "douyin_profile"

            async def generate_profile(self, request):
                nonlocal active, max_active
                user = request.options["user"]
                calls.append(user["_id"])
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                async with lock:
                    active -= 1
                return ProfileResult(
                    source="douyin",
                    project_id=request.project_id,
                    task_id=request.task_id,
                    profiles=[{"sec_uid": user["_id"]}],
                )

        profiles = await DouyinPipeline(_FakeDB(), object())._run_profile_stream(
            project_id="project-1",
            task_id="task-1",
            keyword="目标公司",
            potential_users=[
                {"_id": "sec-1"},
                {"_id": "sec-2"},
                {"_id": "sec-3"},
                {"_id": "sec-4"},
            ],
            tool_state={"douyin_profile_tool": _ProfileTool()},
        )

        assert sorted(calls) == ["sec-1", "sec-2", "sec-3", "sec-4"]
        assert sorted(profile["sec_uid"] for profile in profiles) == [
            "sec-1",
            "sec-2",
            "sec-3",
            "sec-4",
        ]
        assert max_active > 1

    asyncio.run(_run())
