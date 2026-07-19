from api.services.info_collection.tuning import CollectionRuntimeTuning


def test_collection_runtime_tuning_applies_defaults_and_safety_limits() -> None:
    defaults = CollectionRuntimeTuning.from_config({})
    assert defaults.as_dict() == {
        "asset_probe_concurrency": 96,
        "url_probe_concurrency": 64,
        "url_scan_concurrency": 24,
        "copywriting_concurrency": 6,
        "xhs_search_concurrency": 1,
        "company_scan_concurrency": 6,
        "llm_concurrency": 12,
        "llm_quota_cooldown_seconds": 120,
        "llm_quota_max_cooldown_seconds": 900,
    }

    bounded = CollectionRuntimeTuning.from_config(
        {
            "asset_probe_concurrency": 999,
            "url_probe_concurrency": 0,
            "url_scan_concurrency": 99,
            "copywriting_concurrency": "8",
            "xhs_search_concurrency": None,
            "company_scan_concurrency": 99,
            "llm_concurrency": 999,
            "llm_quota_cooldown_seconds": 9999,
            "llm_quota_max_cooldown_seconds": 9999,
        }
    )
    assert bounded.as_dict() == {
        "asset_probe_concurrency": 128,
        "url_probe_concurrency": 1,
        "url_scan_concurrency": 48,
        "copywriting_concurrency": 8,
        "xhs_search_concurrency": 1,
        "company_scan_concurrency": 12,
        "llm_concurrency": 32,
        "llm_quota_cooldown_seconds": 1800,
        "llm_quota_max_cooldown_seconds": 1800,
    }

    overridden = defaults.with_overrides(
        asset_probe_concurrency="120",
        url_scan_concurrency=999,
        copywriting_concurrency=None,
    )
    assert overridden.as_dict() == {
        "asset_probe_concurrency": 120,
        "url_probe_concurrency": 64,
        "url_scan_concurrency": 48,
        "copywriting_concurrency": 6,
        "xhs_search_concurrency": 1,
        "company_scan_concurrency": 6,
        "llm_concurrency": 12,
        "llm_quota_cooldown_seconds": 120,
        "llm_quota_max_cooldown_seconds": 900,
    }
