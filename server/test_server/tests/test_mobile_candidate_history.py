from core.mobile.collect.candidate_history import CandidateHistory


def test_candidate_history_matches_known_title_and_wechat_url() -> None:
    history = CandidateHistory.from_records(
        [
            {
                "fields": {
                    "title": "东部机场集团合作电商平台项目招标公告",
                    "account": "礼采圈",
                },
                "source_url": "https://mp.weixin.qq.com/s/article-token?from=share",
            }
        ]
    )

    assert history.match(
        {
            "title": "东部机场集团合作电商平台项目招标公告",
            "account": "另一个转载账号",
        }
    ) == "相同文章标题已采集"
    assert history.match(
        {"title": "不同文章标题且内容足够具体", "account": "礼采圈"},
        source_url="https://mp.weixin.qq.com/s/article-token?scene=1",
    ) == "原文链接已归档"


def test_candidate_history_does_not_collapse_short_generic_titles() -> None:
    history = CandidateHistory.from_records(
        [{"fields": {"title": "招标公告", "account": "机场发布"}}]
    )

    assert history.match({"title": "招标公告", "account": "机场发布"}) is None


def test_candidate_history_reads_discovery_fields() -> None:
    history = CandidateHistory.from_records(
        [
            {
                "fields": {"title": "浏览器归档后的规范标题"},
                "discovery_fields": {
                    "title": "首都机场2026年度信息系统采购公告",
                    "account": "首都机场集团",
                },
            }
        ]
    )

    assert history.match(
        {
            "title": "首都机场2026年度信息系统采购公告",
            "account": "首都机场集团",
        }
    )


def test_candidate_history_matches_long_truncated_search_title() -> None:
    history = CandidateHistory.from_records(
        [
            {
                "fields": {
                    "title": "东部机场集团航空食品有限公司2026年中秋福利线上采购平台运营项目公开招标公告",
                    "account": "天天向上数字化采购",
                }
            }
        ]
    )

    assert history.match(
        {
            "title": "项目名称：东部机场集团航空食品有限公司2026年中秋福利线上采购平台运营项...",
            "account": "天天向上数字化采购",
        }
    ) == "同公众号的长标题对应已采集"


def test_candidate_history_matches_decorative_prefix_from_same_account() -> None:
    history = CandidateHistory.from_records(
        [
            {
                "fields": {
                    "title": "睿央标讯 | 东部机场集团航空食品有限公司2026年中秋福利线上采购平台运营项目",
                    "account": "睿采云商",
                }
            }
        ]
    )

    assert history.match(
        {
            "title": "东部机场集团航空食品有限公司2026年中秋福利线上采购平台运营项目",
            "account": "睿采云商",
        }
    ) == "同公众号的长标题对应已采集"


def test_candidate_history_keeps_distinct_truncated_projects() -> None:
    history = CandidateHistory.from_records(
        [
            {
                "fields": {
                    "title": "东部机场集团航空食品有限公司2026年中秋福利线上采购平台运营项目公开招标公告",
                    "account": "机场采购发布",
                }
            }
        ]
    )

    assert history.match(
        {
            "title": "项目名称：东部机场集团航空食品有限公司2026年冬季制服采购项目公开招标公告...",
            "account": "机场采购发布",
        }
    ) is None
