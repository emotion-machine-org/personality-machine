from uuid import UUID

from app.services.share_tokens import hash_share_token, verify_share_token


def test_hash_share_token_deterministic():
    share_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    token = "visitor-123"
    first = hash_share_token(token, share_id)
    second = hash_share_token(token, share_id)
    assert first == second
    assert verify_share_token(token, share_id, first)
    assert not verify_share_token("different", share_id, first)
