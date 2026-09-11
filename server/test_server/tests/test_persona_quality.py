"""Empty required fields cannot pass the fictional persona quality gate."""
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
