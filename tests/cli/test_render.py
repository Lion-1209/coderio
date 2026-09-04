from coderio.cli.render import mask_key


def test_mask_key_shows_last4():
    assert mask_key("sk-abcdef1234") == "****1234"
    assert mask_key("short") == "****"
    assert mask_key("") == "****"
