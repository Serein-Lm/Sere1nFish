"""Opt-in persistence contracts against a disposable, explicitly supplied DB."""
import os
import uuid

os.environ.setdefault("SEREINFISH_RUN_KIND", "test")

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def isolated_mongo_db():
    uri = os.environ.get("SEREINFISH_TEST_MONGODB_URI", "")
    if not uri:
        pytest.skip("Set SEREINFISH_TEST_MONGODB_URI to a disposable MongoDB")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "sereinfish_test_" + uuid.uuid4().hex
    try:
        await client.admin.command("ping")
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()
