"""Empty required fields cannot pass the fictional persona quality gate."""
import pytest

from api.services.persona_quality import _profile_quality_issues


def test_quality_rejects_empty_scalar_nested_education_and_list_items():
    issues = _profile_quality_issues({
        "name": "  ", "industry": "", "communication_style": "  ",
        "education": {"school": "示例学校", "degree": "本科", "major": "管理", "graduation_year": " "},
        "interests": ["阅读", " "],
    })
    assert "name 不能为空" in issues
    assert "industry 不能为空" in issues
    assert "communication_style 不能为空" in issues
    assert "education.graduation_year 不能为空" in issues
    assert "interests 包含空白条目" in issues


def test_fictional_contact_and_domain_are_not_required_real_identity_data():
    issues = _profile_quality_issues({"contact": {}, "company_root_domain": ""})
    assert not any(issue.startswith("contact") or issue.startswith("company_root_domain") for issue in issues)


@pytest.mark.parametrize("summary", [
    "许安然，男，24岁，虚构人物，在能源公司工作。",
    "许安然，现年24岁，主要负责项目协调。",
])
def test_summary_age_must_match_preserved_identity(summary):
    issues = _profile_quality_issues({"name": "许安然", "age": 25, "summary": summary})
    assert "summary 人物年龄24岁与age字段25岁不一致" in issues


@pytest.mark.parametrize("summary", [
    "许安然，男，25岁，虚构人物，在能源公司工作。",
    "许安然是一名项目工程师，育有3岁的女儿。",
    "许安然，24岁时参与了第一个项目，现年25岁。",
    "许安然，24岁那年进入项目组。",
])
def test_summary_age_check_does_not_confuse_relatives_or_historical_ages(summary):
    issues = _profile_quality_issues({"name": "许安然", "age": 25, "summary": summary})
    assert not any("summary 人物年龄" in issue for issue in issues)
