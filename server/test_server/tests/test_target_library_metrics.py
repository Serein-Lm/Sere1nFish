import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from api.dao import target_library as dao


def test_bulk_archive_metrics_keep_alias_dedup_and_version_counts():
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    calls = []

    class Collection:
        def __init__(self, name):
            self.name = name

        def cursor(self, rows):
            async def to_list(_):
                return rows
            return SimpleNamespace(to_list=to_list)

        def aggregate(self, pipeline):
            calls.append((self.name, pipeline))
            assert not any('$lookup' in stage for stage in pipeline)
            if self.name == dao.SOURCE_DOCUMENT_LINKS_COLLECTION:
                return self.cursor([{'_id': {'target_id': 'a', 'document_id': 'd1'}},
                                    {'_id': {'target_id': 'a', 'document_id': 'd2'}},
                                    {'_id': {'target_id': 'b', 'document_id': 'd1'}},
                                    {'_id': {'target_id': 'b', 'document_id': 'missing'}}])
            if self.name == dao.SOURCE_DOCUMENT_VERSIONS_COLLECTION:
                assert pipeline[0]['$match']['status'] == 'ready'
                return self.cursor([{'_id': 'd1', 'count': 3}])
            return self.cursor([{'_id': 'a', 'mobile_count': 2}])

        def find(self, query, projection):
            assert set(projection) == {'_id', 'document_id', 'first_seen_at', 'last_seen_at'}
            return self.cursor([{'document_id': 'd1', 'first_seen_at': now, 'last_seen_at': now},
                                {'document_id': 'd2', 'first_seen_at': now, 'last_seen_at': now}])

    class DB:
        def __getitem__(self, name):
            return Collection(name)

    async def run():
        assert await dao.archive_metrics(DB(), []) == {}
        result = await dao.archive_metrics(DB(), [
            {'target_id': 'a', 'member_target_ids': ['a', 'alias-a']},
            {'target_id': 'b', 'member_target_ids': ['b']},
        ])
        assert result['a']['document_count'] == 2
        assert result['a']['version_count'] == 3
        assert result['a']['change_count'] == 2
        assert result['a']['mobile_count'] == 2
        assert result['b']['document_count'] == 1
        assert result['b']['version_count'] == 3
        assert result['a']['first_archived_at'] == now
        assert len(calls) == 3
    asyncio.run(run())
