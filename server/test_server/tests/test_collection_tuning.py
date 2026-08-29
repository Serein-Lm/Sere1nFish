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
        "company_dispatch_concurrency": 24,
        "recovery_group_concurrency": 3,
        "scholar_concurrency": 2,
        "llm_concurrency": 12,
        "url_scan_agent_timeout_seconds": 900,
        "website_crawl_concurrency": 12,
        "website_archive_concurrency": 4,
        "website_crawl_max_pages": 1200,
        "website_crawl_max_documents": 400,
        "website_crawl_max_depth": 5,
        "llm_quota_cooldown_seconds": 120,
        "llm_quota_max_cooldown_seconds": 900,
        "llm_standard_start_interval_seconds": 0.2,
    }

    bounded = CollectionRuntimeTuning.from_config(
        {
            "asset_probe_concurrency": 999,
            "url_probe_concurrency": 0,
            "url_scan_concurrency": 99,
            "copywriting_concurrency": "8",
            "xhs_search_concurrency": None,
            "company_scan_concurrency": 99,
            "company_dispatch_concurrency": 999,
            "recovery_group_concurrency": 99,
            "scholar_concurrency": 99,
            "llm_concurrency": 999,
            "url_scan_agent_timeout_seconds": 9999,
            "website_crawl_concurrency": 999,
            "website_archive_concurrency": 999,
            "website_crawl_max_pages": 99999,
            "website_crawl_max_documents": 99999,
            "website_crawl_max_depth": 999,
            "llm_quota_cooldown_seconds": 9999,
            "llm_quota_max_cooldown_seconds": 9999,
            "llm_standard_start_interval_seconds": 999,
        }
    )
    assert bounded.as_dict() == {
        "asset_probe_concurrency": 128,
        "url_probe_concurrency": 1,
        "url_scan_concurrency": 48,
        "copywriting_concurrency": 8,
        "xhs_search_concurrency": 1,
        "company_scan_concurrency": 12,
        "company_dispatch_concurrency": 64,
        "recovery_group_concurrency": 6,
        "scholar_concurrency": 4,
        "llm_concurrency": 32,
        "url_scan_agent_timeout_seconds": 1500,
        "website_crawl_concurrency": 48,
        "website_archive_concurrency": 16,
        "website_crawl_max_pages": 10000,
        "website_crawl_max_documents": 3000,
        "website_crawl_max_depth": 10,
        "llm_quota_cooldown_seconds": 1800,
        "llm_quota_max_cooldown_seconds": 1800,
        "llm_standard_start_interval_seconds": 2.0,
    }

    overridden = defaults.with_overrides(
        asset_probe_concurrency="120",
        url_scan_concurrency=999,
        copywriting_concurrency=None,
        url_scan_agent_timeout_seconds=20,
    )
    assert overridden.as_dict() == {
        "asset_probe_concurrency": 120,
        "url_probe_concurrency": 64,
        "url_scan_concurrency": 48,
        "copywriting_concurrency": 6,
        "xhs_search_concurrency": 1,
        "company_scan_concurrency": 6,
        "company_dispatch_concurrency": 24,
        "recovery_group_concurrency": 3,
        "scholar_concurrency": 2,
        "llm_concurrency": 12,
        "url_scan_agent_timeout_seconds": 60,
        "website_crawl_concurrency": 12,
        "website_archive_concurrency": 4,
        "website_crawl_max_pages": 1200,
        "website_crawl_max_documents": 400,
        "website_crawl_max_depth": 5,
        "llm_quota_cooldown_seconds": 120,
        "llm_quota_max_cooldown_seconds": 900,
        "llm_standard_start_interval_seconds": 0.2,
    }
