"""M3 tests: API key generation, hashing, and verification."""

from __future__ import annotations

import string

import pytest

from app.core import api_keys


def test_generate_api_key_format():
    full_token, key_prefix, key_hash = api_keys.generate_api_key()
    assert full_token.startswith("syn_live_")
    # prefix is syn_live_ + 8 random chars
    assert key_prefix.startswith("syn_live_")
    assert len(key_prefix) == len("syn_live_") + 8
    # hash is 64 hex chars (SHA-256)
    assert len(key_hash) == 64
    assert all(c in string.hexdigits for c in key_hash)


def test_generate_api_key_uniqueness():
    keys = {api_keys.generate_api_key()[0] for _ in range(100)}
    assert len(keys) == 100  # all unique


def test_generate_api_key_randomness():
    # Verify the visible random part uses alnum
    _, key_prefix, _ = api_keys.generate_api_key()
    random_part = key_prefix.split("_")[-1]
    assert len(random_part) == 8
    assert all(c in string.ascii_letters + string.digits for c in random_part)


def test_hash_api_key_deterministic():
    token = "syn_live_abcdefgh_" + "x" * 43
    h1 = api_keys.hash_api_key(token)
    h2 = api_keys.hash_api_key(token)
    assert h1 == h2
    assert len(h1) == 64


def test_hash_api_key_different_for_different_tokens():
    h1 = api_keys.hash_api_key("syn_live_abcdefgh_" + "x" * 43)
    h2 = api_keys.hash_api_key("syn_live_abcdefgh_" + "y" * 43)
    assert h1 != h2


def test_verify_api_key_correct():
    token, _, key_hash = api_keys.generate_api_key()
    assert api_keys.verify_api_key(token, key_hash) is True


def test_verify_api_key_wrong():
    _, _, key_hash = api_keys.generate_api_key()
    wrong = "syn_live_zzzzzzzz_" + "a" * 43
    assert api_keys.verify_api_key(wrong, key_hash) is False


def test_verify_api_key_constant_time_safe():
    # Both should return False for wrong tokens
    _, _, key_hash = api_keys.generate_api_key()
    assert api_keys.verify_api_key("syn_live_aaaaaa_aaa", key_hash) is False
    assert api_keys.verify_api_key("", key_hash) is False


def test_is_valid_format_valid():
    token, _, _ = api_keys.generate_api_key()
    assert api_keys.is_valid_format(token) is True


def test_is_valid_format_with_underscore_in_secret():
    # secrets.token_urlsafe can produce underscores
    token = "syn_live_aB3xK9pQ_" + "a" * 20 + "_" + "b" * 20
    assert api_keys.is_valid_format(token) is True


def test_is_valid_format_rejects_empty():
    assert api_keys.is_valid_format("") is False


def test_is_valid_format_rejects_wrong_namespace():
    assert api_keys.is_valid_format("other_live_abcdefgh_" + "x" * 43) is False


def test_is_valid_format_rejects_wrong_mode():
    assert api_keys.is_valid_format("syn_test_abcdefgh_" + "x" * 43) is False


def test_is_valid_format_rejects_short_prefix():
    assert api_keys.is_valid_format("syn_live_abc_" + "x" * 43) is False


def test_is_valid_format_rejects_short_secret():
    assert api_keys.is_valid_format("syn_live_abcdefgh_short") is False


def test_visible_prefix():
    token, key_prefix, _ = api_keys.generate_api_key()
    assert api_keys.visible_prefix(token) == key_prefix
