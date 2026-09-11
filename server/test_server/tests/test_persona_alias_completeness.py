import pytest
from pydantic import ValidationError

from api.services.persona_quality import _profile_quality_issues
from Sere1nGraph.graph.skills.schemas import RichFictionalPersonaProfile
from test_server.tests.test_ai_hub_payload import _rich_fictional_profile


def test_generated_profile_requires_a_nonempty_nickname_list():
    profile = _rich_fictional_profile().model_dump()
    profile['aliases'] = []
    with pytest.raises(ValidationError):
        RichFictionalPersonaProfile.model_validate(profile)
    assert 'aliases 不能为空' in _profile_quality_issues(profile)


def test_blank_nickname_is_rejected_by_shared_quality_gate():
    profile = _rich_fictional_profile().model_dump()
    profile['aliases'] = [' ']
    assert 'aliases 包含空白条目' in _profile_quality_issues(profile)
