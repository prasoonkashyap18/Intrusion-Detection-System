"""Tests for app.services.password_hashing: PBKDF2-HMAC-SHA256 hashing,
verification, and safe handling of malformed stored hashes.
"""

from __future__ import annotations

import pytest

from app.services.password_hashing import ALGORITHM, hash_password, verify_password


class TestHashPassword:
    def test_produces_a_self_describing_string(self):
        hashed = hash_password("correct horse battery staple")
        parts = hashed.split("$")
        assert len(parts) == 4
        assert parts[0] == ALGORITHM

    def test_never_stores_the_plaintext(self):
        password = "correct horse battery staple"
        hashed = hash_password(password)
        assert password not in hashed

    def test_two_hashes_of_the_same_password_differ(self):
        # Different random salts each time -- proves salting is real.
        first = hash_password("same-password")
        second = hash_password("same-password")
        assert first != second

    def test_rejects_an_empty_password(self):
        with pytest.raises(ValueError):
            hash_password("")


class TestVerifyPassword:
    def test_correct_password_verifies(self):
        hashed = hash_password("my-secret-password")
        assert verify_password("my-secret-password", hashed) is True

    def test_wrong_password_does_not_verify(self):
        hashed = hash_password("my-secret-password")
        assert verify_password("not-the-password", hashed) is False

    def test_malformed_stored_hash_returns_false_not_an_exception(self):
        assert verify_password("anything", "not-a-real-hash") is False

    def test_unrecognized_algorithm_tag_returns_false(self):
        assert verify_password("anything", "md5$1$abcd$abcd") is False

    def test_empty_stored_hash_returns_false(self):
        assert verify_password("anything", "") is False

    def test_respects_the_iteration_count_embedded_in_the_hash(self):
        hashed = hash_password("a-password", iterations=1000)
        assert verify_password("a-password", hashed) is True
