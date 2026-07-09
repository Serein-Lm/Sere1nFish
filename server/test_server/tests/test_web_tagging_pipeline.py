import asyncio

from api.services.info_collection import SearchResult
from api.services.web_tagging_pipeline import WebTaggingPipeline


class _FakeDB:
    pass


def test_web_tagging_hunter_stage_uses_hunter_tool(monkeypatch):
    async def _run():
        from api.services.info_collection.factory import InfoCollectionToolFactory

        calls = []

        class _HunterTool:
            name = "hunter_search_probe"

            async def search(self, request):
                calls.append(request)
                return SearchResult(
                    source="hunter",
                    query=request.query,
                    items=[{"url": "https://a.example"}],
                )

        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_hunter_search_tool",
            lambda self: _HunterTool(),
        )

        result = await WebTaggingPipeline(_FakeDB(), object())._stage_hunter_and_probe(
            company_name="目标公司",
            max_urls=20,
            probe_concurrency=6,
            probe_timeout=3.0,
        )

        assert result == [{"url": "https://a.example"}]
        req = calls[0]
        assert req.source == "hunter"
        assert req.query == "目标公司"
        assert req.limit == 20
        assert req.options["search_type"] == "icp"
        assert req.options["probe_concurrency"] == 6
        assert req.options["probe_timeout"] == 3.0

    asyncio.run(_run())


def test_web_tagging_pipeline_uses_url_scan_pipeline(monkeypatch):
    async def _run():
        from api.services.url_scan_pipeline import UrlScanPipeline

        calls = []

        async def fake_scan_urls(
            self,
            project_id,
            alive_urls,
            task_id="",
            num_workers=3,
            on_result=None,
        ):
            calls.append({
                "project_id": project_id,
                "alive_urls": list(alive_urls),
                "task_id": task_id,
                "num_workers": num_workers,
                "has_on_result": on_result is not None,
            })
            return [
                {
                    "success": True,
                    "url": item["url"],
                    "data": {"findings": [{"label": item["url"]}]},
                }
                for item in alive_urls
            ]

        monkeypatch.setattr(UrlScanPipeline, "scan_urls", fake_scan_urls)

        pipeline = WebTaggingPipeline(_FakeDB(), object())

        async def fake_hunter_and_probe(company_name, max_urls, probe_concurrency, probe_timeout):
            assert company_name == "目标公司"
            assert max_urls == 50
            assert probe_concurrency == 20
            assert probe_timeout == 10.0
            return [
                {"url": "https://a.example"},
                {"url": "https://b.example"},
                {"url": "https://c.example"},
            ]

        monkeypatch.setattr(pipeline, "_stage_hunter_and_probe", fake_hunter_and_probe)

        result = await pipeline.run_pipeline(
            project_id="project-1",
            company_name="目标公司",
            max_urls=50,
            max_tagging_urls=2,
            task_id="task-1",
        )

        assert result["alive_count"] == 3
        assert result["tagged_count"] == 2
        assert result["findings_count"] == 2
        assert calls == [{
            "project_id": "project-1",
            "alive_urls": [
                {"url": "https://a.example"},
                {"url": "https://b.example"},
            ],
            "task_id": "task-1",
            "num_workers": 2,
            "has_on_result": False,
        }]

    asyncio.run(_run())


def test_web_tagging_single_url_uses_url_scan_pipeline(monkeypatch):
    async def _run():
        from api.services.url_scan_pipeline import UrlScanPipeline

        calls = []

        async def fake_scan_urls(
            self,
            project_id,
            alive_urls,
            task_id="",
            num_workers=3,
            on_result=None,
        ):
            calls.append({
                "project_id": project_id,
                "alive_urls": list(alive_urls),
                "task_id": task_id,
                "num_workers": num_workers,
            })
            return [{
                "success": True,
                "url": alive_urls[0]["url"],
                "data": {"findings": []},
            }]

        monkeypatch.setattr(UrlScanPipeline, "scan_urls", fake_scan_urls)

        result = await WebTaggingPipeline(_FakeDB(), object()).run_single_url(
            project_id="project-1",
            url="https://single.example",
        )

        assert result["success"] is True
        assert result["url"] == "https://single.example"
        assert calls == [{
            "project_id": "project-1",
            "alive_urls": [{"url": "https://single.example"}],
            "task_id": "web_tagging_single",
            "num_workers": 1,
        }]

    asyncio.run(_run())
