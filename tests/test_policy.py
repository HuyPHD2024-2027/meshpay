from meshpay.policy import (
    PolicyStore,
    build_default_policy,
    canonical_policy_hash,
    default_authority_keys,
    sign_policy,
)


def _signed(policy, authorities):
    keys = default_authority_keys(authorities)
    return [sign_policy(policy, authority, keys[authority]) for authority in authorities]


def test_policy_canonical_hash_is_stable_without_signatures():
    authorities = ["auth1", "auth2", "auth3", "auth4", "auth5"]
    policy = build_default_policy(authorities, epoch=1, valid_from=0, valid_until=100)
    signed = sign_policy(policy, "auth1", default_authority_keys(authorities)["auth1"])

    assert canonical_policy_hash(policy) == canonical_policy_hash(signed)


def test_policy_activates_only_after_quorum_and_ignores_duplicates():
    authorities = ["auth1", "auth2", "auth3", "auth4", "auth5"]
    policy = build_default_policy(authorities, epoch=1, valid_from=0, valid_until=100)
    store = PolicyStore(default_authority_keys(authorities), now_func=lambda: 10)
    fragments = _signed(policy, authorities)

    result = store.add_policy_fragment(fragments[0])
    assert result.accepted
    assert not result.active
    assert result.signature_count == 1
    assert result.required_signatures == 4

    duplicate = store.add_policy_fragment(fragments[0])
    assert duplicate.signature_count == 1
    assert not duplicate.active

    for fragment in fragments[1:3]:
        result = store.add_policy_fragment(fragment)
        assert result.accepted
        assert not result.active

    result = store.add_policy_fragment(fragments[3])
    assert result.active
    assert result.signature_count == 4
    assert store.active_policy is not None


def test_old_epoch_and_expired_policies_are_rejected():
    authorities = ["auth1", "auth2", "auth3", "auth4", "auth5"]
    keys = default_authority_keys(authorities)
    store = PolicyStore(keys, now_func=lambda: 10)

    policy_epoch_2 = build_default_policy(authorities, epoch=2, valid_from=0, valid_until=100)
    for authority in authorities[:4]:
        store.add_policy_fragment(sign_policy(policy_epoch_2, authority, keys[authority]))
    assert store.active_epoch == 2

    old_policy = build_default_policy(authorities, epoch=1, valid_from=0, valid_until=100)
    old_result = store.add_policy_fragment(sign_policy(old_policy, "auth1", keys["auth1"]))
    assert not old_result.accepted
    assert old_result.reason == "old_epoch"

    expired_store = PolicyStore(keys, now_func=lambda: 200)
    expired_policy = build_default_policy(authorities, epoch=3, valid_from=0, valid_until=100)
    expired_result = expired_store.add_policy_fragment(sign_policy(expired_policy, "auth1", keys["auth1"]))
    assert not expired_result.accepted
    assert expired_result.reason == "expired"
