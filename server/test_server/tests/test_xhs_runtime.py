import asyncio
from datetime import datetime, timedelta, timezone

import pytest


class _FakeCollection:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    @staticmethod
    def _compare(actual, operator: str, expected) -> bool:
        if operator == "$ne":
            return actual != expected
        if operator == "$nin":
            return actual not in expected
        if operator == "$exists":
            return (actual is not _MISSING) is bool(expected)
        if operator == "$type":
            return operator == "$type" and expected == "string" and isinstance(actual, str)
        if actual is _MISSING:
            return False
        if operator == "$lte":
            return actual <= expected
        if operator == "$lt":
            return actual < expected
        if operator == "$gte":
            return actual >= expected
        return False

    @classmethod
    def _match(cls, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            if key == "$and":
                if not all(cls._match(doc, part) for part in expected):
                    return False
                continue
            if key == "$or":
                if not any(cls._match(doc, part) for part in expected):
                    return False
                continue
            actual = doc.get(key, _MISSING)
            if isinstance(expected, dict):
                if not all(cls._compare(actual, op, value) for op, value in expected.items()):
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _apply_update(doc: dict, update: dict) -> None:
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value

    async def find_one(self, query: dict):
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def find_one_and_update(self, query: dict, update: dict, **kwargs):
        matches = [(idx, doc) for idx, doc in enumerate(self.docs) if self._match(doc, query)]
        sort = kwargs.get("sort") or []
        if sort:
            matches.sort(key=lambda item: tuple(item[1].get(field) for field, _direction in sort))
        if not matches:
            return None
        idx, doc = matches[0]
        self._apply_update(doc, update)
        self.docs[idx] = doc
        return dict(doc)

    async def update_one(self, query: dict, update: dict):
        for doc in self.docs:
            if self._match(doc, query):
                self._apply_update(doc, update)
                return None
        return None

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.docs if self._match(doc, query))


class _FakeDB:
    def __init__(self, docs: list[dict]) -> None:
        self.collection = _FakeCollection(docs)

    def __getitem__(self, _name: str):
        return self.collection


class _Missing:
    pass


_MISSING = _Missing()


def test_xhs_account_cooldown_rejoins_after_expiry(monkeypatch):
    async def _run():
        from api.services import xhs_runtime

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        async def fake_config():
            return {"account_pool": {"enabled": True, "max_consecutive_failures": 3}}

        monkeypatch.setattr(xhs_runtime, "_now", lambda: now)
        monkeypatch.setattr(xhs_runtime, "get_xhs_runtime_config", fake_config)
        db = _FakeDB([
            {
                "account_name": "cooling",
                "cookie_string": "cookie",
                "is_enabled": True,
                "is_valid": True,
                "is_active": True,
                "cooldown_until": now + timedelta(seconds=30),
                "consecutive_failures": 1,
                "last_used_at": now - timedelta(hours=1),
            }
        ])

        with pytest.raises(RuntimeError):
            await xhs_runtime.select_xhs_account(db, purpose="search")

        db.collection.docs[0]["cooldown_until"] = now - timedelta(seconds=1)
        lease = await xhs_runtime.select_xhs_account(db, purpose="search")

        assert lease.account_name == "cooling"
        assert lease.source == "pool:least_recently_used"

    asyncio.run(_run())


def test_xhs_account_pool_rotates_on_successive_leases(monkeypatch):
    async def _run():
        from api.services import xhs_runtime

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        async def fake_config():
            return {
                "account_pool": {
                    "enabled": True,
                    "strategy": "least_recently_used",
                    "max_consecutive_failures": 3,
                }
            }

        monkeypatch.setattr(xhs_runtime, "_now", lambda: now)
        monkeypatch.setattr(xhs_runtime, "get_xhs_runtime_config", fake_config)
        db = _FakeDB([
            {
                "account_name": "account-a",
                "cookie_string": "cookie-a",
                "is_enabled": True,
                "is_valid": True,
                "last_used_at": now - timedelta(hours=2),
            },
            {
                "account_name": "account-b",
                "cookie_string": "cookie-b",
                "is_enabled": True,
                "is_valid": True,
                "last_used_at": now - timedelta(hours=1),
            },
        ])

        first = await xhs_runtime.select_xhs_account(db, purpose="search")
        second = await xhs_runtime.select_xhs_account(db, purpose="search")

        assert first.account_name == "account-a"
        assert second.account_name == "account-b"
        assert [doc["lease_count"] for doc in db.collection.docs] == [1, 1]

    asyncio.run(_run())


def test_xhs_account_invalidates_only_after_failure_threshold(monkeypatch):
    async def _run():
        from api.services import xhs_runtime

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        async def fake_config():
            return {"account_pool": {"enabled": True, "max_consecutive_failures": 3}}

        monkeypatch.setattr(xhs_runtime, "_now", lambda: now)
        monkeypatch.setattr(xhs_runtime, "get_xhs_runtime_config", fake_config)
        db = _FakeDB([
            {
                "account_name": "risk",
                "cookie_string": "cookie",
                "is_enabled": True,
                "is_valid": True,
                "consecutive_failures": 1,
            }
        ])

        await xhs_runtime.record_xhs_account_result(
            db,
            "risk",
            success=False,
            error="登录失败",
            invalidate=True,
            cooldown_seconds=60,
        )
        assert db.collection.docs[0]["consecutive_failures"] == 2
        assert db.collection.docs[0]["is_valid"] is True
        assert not db.collection.docs[0].get("quarantined_at")

        await xhs_runtime.record_xhs_account_result(
            db,
            "risk",
            success=False,
            error="登录失败",
            invalidate=True,
            cooldown_seconds=60,
        )
        assert db.collection.docs[0]["consecutive_failures"] == 3
        assert db.collection.docs[0]["is_valid"] is False
        assert db.collection.docs[0]["quarantined_at"] == now
        assert "达到阈值 3" in db.collection.docs[0]["quarantine_reason"]

    asyncio.run(_run())
