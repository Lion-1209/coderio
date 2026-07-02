from coderio.skills.triggers import detect_stage, STAGE_SKILL_MAP


def test_implement_stage():
    assert detect_stage("好的，开始实现吧") == "implement"
    assert detect_stage("let's start implementing") == "implement"


def test_commit_stage():
    assert detect_stage("please commit this") == "commit"
    assert detect_stage("提交一下") == "commit"


def test_no_stage():
    assert detect_stage("what files are here?") is None


def test_map_contents():
    assert STAGE_SKILL_MAP["implement"] == "executing-plans"
    assert STAGE_SKILL_MAP["commit"] == "commit-message"
    assert len(STAGE_SKILL_MAP) == 2
