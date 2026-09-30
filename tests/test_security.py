from app.telegram.security import is_owner


def test_owner_whitelist():
    assert is_owner(42, 42)
    assert not is_owner(43, 42)
    assert not is_owner(None, 42)
