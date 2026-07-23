from coderio.skills.triggers import STAGE_SKILL_MAP, detect_stage


def test_implement_stage():
    assert detect_stage("好的，开始实现吧") == "implement"
    assert detect_stage("let's start implementing") == "implement"


def test_implement_stage_english_variants():
    """English implement signals should also fire (i18n: not CN-only)."""
    assert detect_stage("let's implement the feature") == "implement"
    assert detect_stage("please build the login page") == "implement"
    assert detect_stage("go ahead and code it") == "implement"


def test_commit_stage():
    assert detect_stage("please commit this") == "commit"
    assert detect_stage("提交一下") == "commit"


def test_commit_stage_english_variants():
    """English commit/save signals should also fire."""
    assert detect_stage("save the changes") == "commit"
    assert detect_stage("push it") == "commit"
    assert detect_stage("save this") == "commit"


def test_no_stage():
    assert detect_stage("what files are here?") is None
    assert detect_stage("这是一个什么项目") is None


def test_map_contents():
    assert STAGE_SKILL_MAP["implement"] == "executing-plans"
    assert STAGE_SKILL_MAP["commit"] == "commit-message"
    assert len(STAGE_SKILL_MAP) == 2
