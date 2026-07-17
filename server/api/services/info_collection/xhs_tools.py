"""XHS information collection tool adapters."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from api.services.info_collection.contracts import (
    DetailRequest,
    DetailResult,
    ProfileRequest,
    ProfileResult,
    SearchRequest,
    SearchResult,
    TagRequest,
    TagResult,
)
from core.logger import get_logger


logger = get_logger("api.services.info_collection.xhs_tools")


class XhsSearchTool:
    """Search XHS behind the information-collection tool boundary.

    Account rotation, proxy selection, V2 client creation, crawler fallback and
    persistence stay here so pipeline stages can remain pure orchestration code.
    """

    name = "xhs_search"

    def __init__(
        self,
        *,
        db: Any,
        crawler_factory: Any | None = None,
        runtime_config_loader: Any | None = None,
        account_selector: Any | None = None,
        proxy_selector: Any | None = None,
        result_recorder: Any | None = None,
        client_factory: Any | None = None,
        sleep_func: Any | None = None,
        request_pacer: Any | None = None,
        archive_service: Any | None = None,
    ) -> None:
        self._db = db
        self._crawler_factory = crawler_factory
        self._runtime_config_loader = runtime_config_loader
        self._account_selector = account_selector
        self._proxy_selector = proxy_selector
        self._result_recorder = result_recorder
        self._client_factory = client_factory
        self._sleep = sleep_func or asyncio.sleep
        self._request_pacer = request_pacer
        self._archive_service = archive_service

    @staticmethod
    def _as_int(value: Any, default: int, minimum: int = 1) -> int:
        try:
            return max(minimum, int(value))
        except Exception:
            return max(minimum, default)

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
        return bool(value)

    @staticmethod
    def _extract_publish_time_text(item: dict[str, Any]) -> str:
        corner_tags = item.get("corner_tag_info", [])
        for tag in corner_tags:
            if tag.get("type") == "publish_time":
                return tag.get("text", "")
        return ""

    @classmethod
    def _normalize_v2_item(
        cls,
        *,
        item: dict[str, Any],
        project_id: str,
        task_id: str,
        keyword: str,
        target_id: str = "",
        target_name: str = "",
    ) -> dict[str, Any]:
        note_card = item.get("note_card", {})
        return {
            "project_id": project_id,
            "task_id": task_id,
            "keyword": keyword,
            "target_id": target_id,
            "target_name": target_name,
            "note_id": item.get("id", ""),
            "xsec_token": item.get("xsec_token", ""),
            "xsec_source": item.get("xsec_source", ""),
            "title": note_card.get("display_title", ""),
            "desc": note_card.get("desc", ""),
            "note_type": note_card.get("type", ""),
            "liked_count": note_card.get("interact_info", {}).get("liked_count", "0"),
            "user": note_card.get("user", {}),
            "cover": note_card.get("cover", {}).get("url_default", ""),
            "last_update_time": note_card.get("last_update_time"),
            "corner_tag_info": note_card.get("corner_tag_info", []),
            "publish_time_text": cls._extract_publish_time_text(note_card),
        }

    @classmethod
    def _normalize_crawler_item(
        cls,
        *,
        item: dict[str, Any],
        project_id: str,
        task_id: str,
        keyword: str,
        target_id: str = "",
        target_name: str = "",
    ) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "task_id": task_id,
            "keyword": keyword,
            "target_id": target_id,
            "target_name": target_name,
            "note_id": item.get("note_id", ""),
            "xsec_token": item.get("xsec_token", ""),
            "xsec_source": item.get("xsec_source", ""),
            "title": item.get("title", ""),
            "desc": item.get("desc", ""),
            "note_type": item.get("type", ""),
            "liked_count": item.get("liked_count", "0"),
            "user": item.get("user", {}),
            "cover": item.get("cover"),
            "last_update_time": item.get("last_update_time"),
            "corner_tag_info": item.get("corner_tag_info", []),
            "publish_time_text": cls._extract_publish_time_text(item),
        }

    async def _load_runtime_config(self) -> dict[str, Any]:
        if self._runtime_config_loader:
            config = await self._runtime_config_loader()
        else:
            from api.services.xhs_runtime import get_xhs_runtime_config

            config = await get_xhs_runtime_config()
        return config if isinstance(config, dict) else {}

    async def _select_account(
        self,
        *,
        purpose: str,
        runtime_config: dict[str, Any],
        exclude_accounts: list[str],
    ) -> Any:
        if self._account_selector:
            return await self._account_selector(
                self._db,
                purpose=purpose,
                config=runtime_config,
                exclude_accounts=exclude_accounts,
            )
        from api.services.xhs_runtime import select_xhs_account

        return await select_xhs_account(
            self._db,
            purpose=purpose,
            config=runtime_config,
            exclude_accounts=exclude_accounts,
        )

    async def _select_proxy(self, runtime_config: dict[str, Any]) -> Any:
        if self._proxy_selector:
            return await self._proxy_selector(runtime_config)
        from api.services.xhs_runtime import select_xhs_proxy

        return await select_xhs_proxy(runtime_config)

    async def _record_result(self, account_name: str | None, **kwargs: Any) -> None:
        if not account_name:
            return
        if self._result_recorder:
            await self._result_recorder(self._db, account_name, **kwargs)
            return
        from api.services.xhs_runtime import record_xhs_account_result

        await record_xhs_account_result(self._db, account_name, **kwargs)

    async def _wait_for_request(self, purpose: str, runtime_config: dict[str, Any]) -> None:
        if self._request_pacer:
            await self._request_pacer(purpose, config=runtime_config)
            return
        from api.services.xhs_runtime import wait_for_xhs_request_slot

        await wait_for_xhs_request_slot(purpose, config=runtime_config)

    async def _new_v2_client(self, cookie_string: str, *, proxy_url: str | None, request_timeout: float) -> Any:
        if self._client_factory:
            return self._client_factory(
                cookie_string,
                proxy_url=proxy_url,
                request_timeout=request_timeout,
            )
        from crawler_tools.xhs_client_v2 import XhsClientV2

        return XhsClientV2(
            cookie_string,
            proxy_url=proxy_url,
            request_timeout=request_timeout,
        )

    async def _get_fallback_crawler(self) -> Any:
        if self._crawler_factory:
            return await self._crawler_factory()
        from crawler_tools.xhs_crawler import create_crawler

        return await create_crawler()

    async def _archive_search_page(
        self,
        *,
        payload: Any,
        project_id: str,
        task_id: str,
        keyword: str,
        page: int,
        provider: str,
    ) -> dict[str, str]:
        if self._archive_service is None:
            return {}
        return await self._archive_service.archive_json(
            payload,
            kind="search",
            project_id=project_id,
            task_id=task_id,
            source_id=f"{task_id}:page:{page}",
            meta={"keyword": keyword, "page": page, "provider": provider},
        )

    @staticmethod
    async def _safe_close(client: Any) -> None:
        close = getattr(client, "close", None)
        if close:
            try:
                await close()
            except Exception:
                pass

    @staticmethod
    def _proxy_log_context(proxy: Any) -> Any:
        return proxy.to_dict() if hasattr(proxy, "to_dict") else proxy

    async def search(self, request: SearchRequest) -> SearchResult:
        from api.dao import xhs as xhs_dao
        from api.services.xhs_runtime import XhsRequestPolicy, classify_xhs_account_error

        project_id = request.project_id
        task_id = request.task_id
        keyword = request.query
        max_notes = request.limit
        sort_type = str(request.options.get("sort_type", "time_descending"))
        target_id = str(request.options.get("target_id") or "")
        target_name = str(request.options.get("target_name") or "")

        runtime_config = await self._load_runtime_config()
        account_pool = runtime_config.get("account_pool", {})
        if not isinstance(account_pool, dict):
            account_pool = {}
        proxy_config = runtime_config.get("proxy_pool", {})
        if not isinstance(proxy_config, dict):
            proxy_config = {}

        request_policy = XhsRequestPolicy.from_config(runtime_config)
        page_size = request_policy.page_size
        pages_per_account = self._as_int(account_pool.get("search_pages_per_account"), 1)
        retries_per_page = min(
            5,
            max(2, self._as_int(account_pool.get("search_retries_per_page"), 2)),
        )
        total_pages = min(
            request_policy.max_pages_per_keyword,
            max(1, (max_notes + page_size - 1) // page_size),
        )
        fallback_enabled = self._as_bool(
            account_pool.get("search_fallback_enabled"),
            default=False,
        )
        health_check_enabled = self._as_bool(
            account_pool.get("search_health_check_enabled"),
            default=False,
        )
        sort_map = {
            "time_descending": "time_descending",
            "general": "general",
            "popularity_descending": "popularity_descending",
        }
        request_timeout = self._as_float(proxy_config.get("request_timeout"), 30.0)

        logger.info(
            f"[XHS] 搜索动态调度 | keyword='{keyword}' pages={total_pages} "
            f"pages_per_account={pages_per_account} retries_per_page={retries_per_page}"
        )

        notes_to_insert: list[dict[str, Any]] = []
        seen_note_ids: set[str] = set()
        current_account = None
        current_proxy = None
        current_client = None
        current_client_pages_left = 0
        last_v2_error: Exception | None = None

        async def _close_v2_client() -> None:
            nonlocal current_account, current_proxy, current_client, current_client_pages_left
            await self._safe_close(current_client)
            current_account = None
            current_proxy = None
            current_client = None
            current_client_pages_left = 0

        async def _lease_v2_client(exclude_accounts: list[str]) -> None:
            nonlocal current_account, current_proxy, current_client, current_client_pages_left

            await _close_v2_client()
            current_account = await self._select_account(
                purpose="search",
                runtime_config=runtime_config,
                exclude_accounts=exclude_accounts,
            )
            current_proxy = await self._select_proxy(runtime_config)
            current_client = await self._new_v2_client(
                current_account.cookie_string,
                proxy_url=current_proxy.proxy_url,
                request_timeout=request_timeout,
            )
            logger.info(
                f"[XHS] 搜索账号租用 | account={current_account.account_name} "
                f"source={current_account.source} proxy={self._proxy_log_context(current_proxy)}"
            )
            if health_check_enabled and hasattr(current_client, "pong"):
                await self._wait_for_request("search_health", runtime_config)
                if not await current_client.pong():
                    await self._record_result(
                        current_account.account_name,
                        success=False,
                        error="V2 Cookie 验证失败",
                        invalidate=True,
                        cooldown_seconds=900,
                    )
                    await _close_v2_client()
                    raise RuntimeError("V2 Cookie 验证失败")
                await self._record_result(current_account.account_name, success=True)
            current_client_pages_left = pages_per_account

        # V2: 每页动态调度账号；仅排除当前页已失败账号，冷却到期后自动回到候选集合。
        for page in range(1, total_pages + 1):
            page_items: list[dict[str, Any]] | None = None
            page_payload: dict[str, Any] | None = None
            tried_this_page: list[str] = []
            stop_after_page = False
            for attempt in range(1, retries_per_page + 1):
                try:
                    if (
                        current_client is None
                        or current_account is None
                        or current_client_pages_left <= 0
                    ):
                        await _lease_v2_client(list(dict.fromkeys(tried_this_page)))
                    await self._wait_for_request("search", runtime_config)
                    result = await current_client.search_notes(
                        keyword=keyword,
                        page=page,
                        page_size=page_size,
                        sort=sort_map.get(sort_type, "general"),
                    )
                    page_items = result.get("items", []) or []
                    page_payload = result
                    stop_after_page = result.get("has_more") is False
                    await self._record_result(current_account.account_name, success=True)
                    current_client_pages_left -= 1
                    logger.info(
                        f"[XHS] V2 搜索第{page}页成功 | account={current_account.account_name} "
                        f"items={len(page_items)} 累计={len(notes_to_insert)}"
                    )
                    break
                except Exception as exc:
                    last_v2_error = exc
                    account_name = getattr(current_account, "account_name", None)
                    if account_name:
                        tried_this_page.append(account_name)
                        decision = classify_xhs_account_error(exc, config=runtime_config)
                        await self._record_result(
                            account_name,
                            success=False,
                            error=str(exc),
                            invalidate=decision.invalidate,
                            cooldown_seconds=decision.cooldown_seconds,
                        )
                        logger.warning(
                            f"[XHS] V2 搜索第{page}页失败 attempt={attempt}/{retries_per_page} "
                            f"account={account_name} reason={decision.reason}: {exc}"
                        )
                    else:
                        logger.warning(
                            f"[XHS] V2 搜索第{page}页账号租用失败 attempt={attempt}/{retries_per_page}: {exc}"
                        )
                    await _close_v2_client()
                    if attempt < retries_per_page:
                        await self._sleep(random.uniform(1, 2))

            if page_items is None:
                logger.warning(f"[XHS] V2 第{page}页多账号重试后仍失败")
                break
            if not page_items:
                break

            archive_ref: dict[str, str] = {}
            archive_error = ""
            try:
                archive_ref = await self._archive_search_page(
                    payload=page_payload or {"items": page_items},
                    project_id=project_id,
                    task_id=task_id,
                    keyword=keyword,
                    page=page,
                    provider="v2",
                )
            except Exception as exc:
                archive_error = str(exc)
                logger.warning(
                    "[XHS] 搜索原始响应归档失败 keyword='%s' page=%s: %s",
                    keyword,
                    page,
                    exc,
                )

            for item in page_items:
                note_data = self._normalize_v2_item(
                    item=item,
                    project_id=project_id,
                    task_id=task_id,
                    keyword=keyword,
                    target_id=target_id,
                    target_name=target_name,
                )
                note_id = note_data.get("note_id", "")
                if not note_id or note_id in seen_note_ids:
                    continue
                note_data["raw_search_item"] = item
                note_data["search_payload_object_id"] = archive_ref.get("storage_object_id", "")
                note_data["search_payload_url"] = archive_ref.get("url", "")
                note_data["search_archive_error"] = archive_error
                note_data["search_page"] = page
                note_data["search_provider"] = "v2"
                seen_note_ids.add(note_id)
                notes_to_insert.append(note_data)
                if len(notes_to_insert) >= max_notes:
                    break
            if len(notes_to_insert) >= max_notes or stop_after_page:
                break
            await self._sleep(random.uniform(0.8, 1.6))

        await _close_v2_client()

        if not notes_to_insert and fallback_enabled:
            if last_v2_error:
                logger.warning(f"[XHS] V2 搜索无结果，启用 MediaCrawler fallback: {last_v2_error}")
            else:
                logger.info("[XHS] V2 搜索无结果，启用 MediaCrawler fallback 做兼容验证")

            crawler = await self._get_fallback_crawler()
            for page in range(1, total_pages + 1):
                page_items = None
                tried_this_page: list[str] = []
                for attempt in range(1, retries_per_page + 1):
                    account = None
                    try:
                        account = await self._select_account(
                            purpose="search_fallback",
                            runtime_config=runtime_config,
                            exclude_accounts=list(dict.fromkeys(tried_this_page)),
                        )
                        proxy = await self._select_proxy(runtime_config)
                        if hasattr(crawler, "config"):
                            crawler.config.proxy_url = proxy.proxy_url
                        logger.info(
                            f"[XHS] fallback 搜索账号租用 | page={page} account={account.account_name} "
                            f"source={account.source} proxy={self._proxy_log_context(proxy)}"
                        )
                        await self._wait_for_request("search_fallback_login", runtime_config)
                        login_result = await crawler.login_by_cookie_string(account.cookie_string)
                        if not login_result.success:
                            decision = classify_xhs_account_error(login_result.message, config=runtime_config)
                            await self._record_result(
                                account.account_name,
                                success=False,
                                error=login_result.message,
                                invalidate=True,
                                cooldown_seconds=decision.cooldown_seconds,
                            )
                            tried_this_page.append(account.account_name)
                            raise RuntimeError(f"登录失败: {login_result.message}")

                        await self._wait_for_request("search_fallback", runtime_config)
                        search_result = await crawler.search_notes(
                            keyword=keyword,
                            page=page,
                            page_size=page_size,
                            sort_type=sort_type,
                        )
                        if not search_result.success:
                            decision = classify_xhs_account_error(search_result.message, config=runtime_config)
                            await self._record_result(
                                account.account_name,
                                success=False,
                                error=search_result.message,
                                invalidate=decision.invalidate,
                                cooldown_seconds=decision.cooldown_seconds,
                            )
                            tried_this_page.append(account.account_name)
                            raise RuntimeError(f"搜索失败: {search_result.message}")

                        await self._record_result(account.account_name, success=True)
                        page_items = search_result.items
                        logger.info(
                            f"[XHS] fallback 搜索第{page}页成功 | account={account.account_name} "
                            f"items={len(page_items)}"
                        )
                        break
                    except Exception as exc:
                        logger.warning(
                            f"[XHS] fallback 搜索第{page}页失败 attempt={attempt}/{retries_per_page}: {exc}"
                        )
                        if account and account.account_name not in tried_this_page:
                            tried_this_page.append(account.account_name)
                        if attempt < retries_per_page:
                            await self._sleep(random.uniform(2, 4))

                if page_items is None or not page_items:
                    break

                fallback_payload = {
                    "success": bool(getattr(search_result, "success", True)),
                    "message": str(getattr(search_result, "message", "") or ""),
                    "items": page_items,
                }
                archive_ref: dict[str, str] = {}
                archive_error = ""
                try:
                    archive_ref = await self._archive_search_page(
                        payload=fallback_payload,
                        project_id=project_id,
                        task_id=task_id,
                        keyword=keyword,
                        page=page,
                        provider="mediacrawler",
                    )
                except Exception as exc:
                    archive_error = str(exc)
                    logger.warning(
                        "[XHS] fallback 原始响应归档失败 keyword='%s' page=%s: %s",
                        keyword,
                        page,
                        exc,
                    )

                for item in page_items:
                    note_data = self._normalize_crawler_item(
                        item=item,
                        project_id=project_id,
                        task_id=task_id,
                        keyword=keyword,
                        target_id=target_id,
                        target_name=target_name,
                    )
                    note_id = note_data.get("note_id", "")
                    if not note_id or note_id in seen_note_ids:
                        continue
                    note_data["raw_search_item"] = item
                    note_data["search_payload_object_id"] = archive_ref.get("storage_object_id", "")
                    note_data["search_payload_url"] = archive_ref.get("url", "")
                    note_data["search_archive_error"] = archive_error
                    note_data["search_page"] = page
                    note_data["search_provider"] = "mediacrawler"
                    seen_note_ids.add(note_id)
                    notes_to_insert.append(note_data)
                    if len(notes_to_insert) >= max_notes:
                        break
                if len(notes_to_insert) >= max_notes:
                    break
                await self._sleep(random.uniform(1.5, 3.0))

        notes_to_insert = notes_to_insert[:max_notes]
        if notes_to_insert:
            await xhs_dao.create_notes_batch(self._db, notes_to_insert)
        logger.info(f"[XHS] 搜索阶段完成 | notes={len(notes_to_insert)}")

        return SearchResult(
            source="xhs",
            query=keyword,
            items=notes_to_insert,
            meta={
                "task_id": task_id,
                "limit": max_notes,
                "sort_type": sort_type,
                "pages": total_pages,
                "page_size": page_size,
                "pages_per_account": pages_per_account,
                "retries_per_page": retries_per_page,
                "fallback_enabled": fallback_enabled,
                "health_check_enabled": health_check_enabled,
            },
        )


class XhsDetailTool:
    """Fetch XHS note detail through the XHS runtime boundary.

    Company scan and future pipelines should call this adapter instead of
    calling ``XhsClientV2`` directly.  Account selection, proxy selection,
    client creation and runtime result recording stay behind this tool.
    """

    name = "xhs_detail"

    def __init__(
        self,
        *,
        v2_client: Any | None = None,
        db: Any | None = None,
        runtime_config_loader: Any | None = None,
        account_selector: Any | None = None,
        proxy_selector: Any | None = None,
        result_recorder: Any | None = None,
        client_factory: Any | None = None,
        request_pacer: Any | None = None,
    ) -> None:
        self._v2_client = v2_client
        self._db = db
        self._runtime_config_loader = runtime_config_loader
        self._account_selector = account_selector
        self._proxy_selector = proxy_selector
        self._result_recorder = result_recorder
        self._client_factory = client_factory
        self._request_pacer = request_pacer
        self._client_lock = asyncio.Lock()
        self._account_name: str | None = None
        self._proxy: Any | None = None
        self._runtime_config: dict[str, Any] = {}

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    async def _load_runtime_config(self) -> dict[str, Any]:
        if self._runtime_config_loader:
            config = await self._runtime_config_loader()
        else:
            from api.services.xhs_runtime import get_xhs_runtime_config

            config = await get_xhs_runtime_config()
        return config if isinstance(config, dict) else {}

    async def _select_account(self, runtime_config: dict[str, Any]) -> Any:
        if self._account_selector:
            return await self._account_selector(
                self._db,
                purpose="detail",
                config=runtime_config,
            )
        from api.services.xhs_runtime import select_xhs_account

        return await select_xhs_account(
            self._db,
            purpose="detail",
            config=runtime_config,
        )

    async def _select_proxy(self, runtime_config: dict[str, Any]) -> Any:
        if self._proxy_selector:
            return await self._proxy_selector(runtime_config)
        from api.services.xhs_runtime import select_xhs_proxy

        return await select_xhs_proxy(runtime_config)

    async def _record_result(
        self,
        *,
        success: bool,
        error: str = "",
        invalidate: bool = False,
        cooldown_seconds: int = 300,
    ) -> None:
        if not self._db or not self._account_name:
            return
        try:
            if self._result_recorder:
                await self._result_recorder(
                    self._db,
                    self._account_name,
                    success=success,
                    error=error,
                    invalidate=invalidate,
                    cooldown_seconds=cooldown_seconds,
                )
                return
            from api.services.xhs_runtime import record_xhs_account_result

            await record_xhs_account_result(
                self._db,
                self._account_name,
                success=success,
                error=error,
                invalidate=invalidate,
                cooldown_seconds=cooldown_seconds,
            )
        except Exception as exc:
            logger.warning(f"[xhs-detail] 账号结果记录失败 account={self._account_name}: {exc}")

    async def _wait_for_request(self, purpose: str) -> None:
        if self._request_pacer:
            await self._request_pacer(purpose, config=self._runtime_config)
            return
        from api.services.xhs_runtime import wait_for_xhs_request_slot

        await wait_for_xhs_request_slot(purpose, config=self._runtime_config)

    async def _new_runtime_client(self) -> Any | None:
        if not self._db:
            return None

        runtime_config = await self._load_runtime_config()
        proxy_config = runtime_config.get("proxy_pool", {})
        if not isinstance(proxy_config, dict):
            proxy_config = {}

        account = await self._select_account(runtime_config)
        proxy = await self._select_proxy(runtime_config)
        self._account_name = account.account_name
        self._proxy = proxy
        self._runtime_config = runtime_config

        request_timeout = self._as_float(proxy_config.get("request_timeout"), 30.0)
        if self._client_factory:
            client = self._client_factory(
                account.cookie_string,
                proxy_url=proxy.proxy_url,
                request_timeout=request_timeout,
            )
        else:
            from crawler_tools.xhs_client_v2 import XhsClientV2

            client = XhsClientV2(
                account.cookie_string,
                proxy_url=proxy.proxy_url,
                request_timeout=request_timeout,
            )

        if hasattr(client, "pong"):
            await self._wait_for_request("detail_health")
        if hasattr(client, "pong") and not await client.pong():
            await self._safe_close(client)
            await self._record_result(
                success=False,
                error="V2 Cookie 验证失败",
                invalidate=True,
                cooldown_seconds=900,
            )
            raise RuntimeError("V2 Cookie 验证失败")

        await self._record_result(success=True)
        logger.info(
            f"[xhs-detail] V2 客户端就绪 | account={self._account_name} "
            f"proxy={proxy.to_dict() if hasattr(proxy, 'to_dict') else proxy}"
        )
        return client

    async def _get_client(self) -> Any | None:
        if self._v2_client:
            return self._v2_client
        async with self._client_lock:
            if self._v2_client:
                return self._v2_client
            self._v2_client = await self._new_runtime_client()
            return self._v2_client

    @staticmethod
    async def _safe_close(client: Any) -> None:
        close = getattr(client, "close", None)
        if not close:
            return
        try:
            await close()
        except Exception:
            pass

    @staticmethod
    def _build_comments_summary(comments: list[dict[str, Any]]) -> str:
        summaries: list[str] = []
        for comment in comments[:10]:
            user_info = comment.get("user_info", {}) if isinstance(comment, dict) else {}
            nickname = user_info.get("nickname", "匿名")
            content = comment.get("content", "") if isinstance(comment, dict) else ""
            create_time = comment.get("create_time", 0) if isinstance(comment, dict) else 0
            time_text = ""
            if create_time:
                try:
                    from datetime import datetime

                    time_text = datetime.fromtimestamp(create_time / 1000).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    time_text = str(create_time)
            summaries.append(f"[{time_text}] {nickname}: {str(content)[:100]}")
        return "\n".join(summaries)

    async def fetch_detail(self, request: DetailRequest) -> DetailResult:
        detail: dict[str, Any] = {}
        comments_data: list[dict[str, Any]] = []
        comments_summary = ""
        client = await self._get_client()
        if client:
            try:
                await self._wait_for_request("detail")
                detail = await client.get_note_by_id(
                    note_id=request.item_id,
                    xsec_token=request.xsec_token,
                    xsec_source=request.xsec_source or "pc_feed",
                )
                await self._record_result(success=True)
                if request.options.get("enable_comments") and hasattr(client, "get_note_all_comments"):
                    await self._wait_for_request("comments")
                    comments_data = await client.get_note_all_comments(
                        note_id=request.item_id,
                        xsec_token=request.xsec_token,
                        max_count=int(request.options.get("max_comments", 20)),
                        crawl_interval=float(request.options.get("comment_interval", 0.5)),
                    )
                    comments_summary = self._build_comments_summary(comments_data)
            except Exception as exc:
                await self._record_result(
                    success=False,
                    error=str(exc),
                    cooldown_seconds=300,
                )
                await self.close()
                raise

        content = detail.get("desc", "") if detail else ""
        image_urls: list[str] = []
        if request.options.get("enable_images", True):
            for image in detail.get("image_list", []) if detail else []:
                url = image.get("url_default") or image.get("url")
                if url:
                    image_urls.append(url)

        return DetailResult(
            source="xhs",
            item_id=request.item_id,
            content=content,
            raw=detail or {},
            images_urls=image_urls,
            comments_summary=comments_summary,
            comments_data=comments_data,
            meta={
                "task_id": request.task_id,
                "xsec_source": request.xsec_source,
                "used_v2": bool(client),
                "account_name": self._account_name,
                "proxy": self._proxy.to_dict() if hasattr(self._proxy, "to_dict") else None,
            },
        )

    async def close(self) -> None:
        client = self._v2_client
        self._v2_client = None
        self._account_name = None
        self._proxy = None
        if client:
            await self._safe_close(client)


class XhsNoteTaggingTool:
    """Tag XHS search result notes through the configured note-tagging agent."""

    name = "xhs_note_tagging"

    def __init__(self, *, pipeline_owner: Any, agent: Any) -> None:
        self._pipeline_owner = pipeline_owner
        self._agent = agent

    async def tag(self, request: TagRequest) -> TagResult:
        from core.observability import observation_context
        from langchain_core.messages import HumanMessage

        keyword = str(request.context.get("keyword") or "")
        input_text = self._pipeline_owner._build_note_tagging_input(
            request.item,
            keyword=keyword,
        )
        with observation_context(
            project_id=request.project_id,
            task_id=request.task_id,
            phase="xhs_note_tagging",
            agent="xhs_note_tagging",
        ):
            raw = await self._agent({"messages": [HumanMessage(content=input_text)]})
        tagging = self._pipeline_owner._parse_agent_response(raw) or {}
        return TagResult(
            source="xhs",
            kind="note",
            item_id=request.item_id,
            tagging=tagging,
            raw=raw,
            meta={"keyword": keyword, "task_id": request.task_id},
        )


class XhsDetailTaggingTool:
    """Tag XHS note details through the configured detail-tagging agent."""

    name = "xhs_detail_tagging"

    def __init__(self, *, pipeline_owner: Any, agent: Any) -> None:
        self._pipeline_owner = pipeline_owner
        self._agent = agent

    async def tag(self, request: TagRequest) -> TagResult:
        from core.observability import observation_context
        from langchain_core.messages import HumanMessage

        content = str(request.context.get("content") or "")
        comments_summary = str(request.context.get("comments_summary") or "")
        input_text = self._pipeline_owner._build_detail_tagging_input(
            request.item,
            content,
            comments_summary,
        )
        with observation_context(
            project_id=request.project_id,
            task_id=request.task_id,
            phase="xhs_detail_tagging",
            agent="xhs_detail_tagging",
        ):
            raw = await self._agent({"messages": [HumanMessage(content=input_text)]})
        tagging = self._pipeline_owner._parse_agent_response(raw) or {}
        return TagResult(
            source="xhs",
            kind="detail",
            item_id=request.item_id,
            tagging=tagging,
            raw=raw,
            meta={"task_id": request.task_id},
        )


class XhsProfileTool:
    """Generate XHS user profiles through the shared XhsPipeline runtime."""

    name = "xhs_profile"

    def __init__(self, pipeline_owner: Any) -> None:
        self._pipeline_owner = pipeline_owner

    async def generate_profile(self, request: ProfileRequest) -> ProfileResult:
        from core.observability import observation_context

        options: dict[str, Any] = {
            "screenshot_concurrency": int(request.options.get("screenshot_concurrency", 1)),
            "profile_concurrency": int(request.options.get("profile_concurrency", 2)),
        }
        target_id = str(request.options.get("target_id") or "")
        if target_id:
            options["target_id"] = target_id
        with observation_context(
            project_id=request.project_id,
            task_id=request.task_id,
            phase="xhs_profile",
            agent="xhs_profile",
        ):
            profiles = await self._pipeline_owner._stage_profile_generation(
                request.task_id,
                request.project_id,
                request.keyword,
                **options,
            )
        return ProfileResult(
            source="xhs",
            project_id=request.project_id,
            task_id=request.task_id,
            profiles=profiles,
            meta={
                "keyword": request.keyword,
                "screenshot_concurrency": int(request.options.get("screenshot_concurrency", 1)),
                "profile_concurrency": int(request.options.get("profile_concurrency", 2)),
            },
        )
