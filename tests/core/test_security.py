"""口令散列：argon2id 首选、PBKDF2 兜底、老散列惰性升级（批 J3）。"""

from __future__ import annotations

import hashlib
import json
import os
import unittest

from app.core.runtime import security
from app.services.user_service import UserService
from tests.harness import IsolatedCase


def _legacy_pbkdf2(password: str, iterations: int | None = None) -> str:
    """造一份老格式（PBKDF2 JSON）散列，模拟升级前的库。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations or security.ITERATIONS)
    return json.dumps({"iter": iterations or security.ITERATIONS, "salt": salt.hex(), "hash": digest.hex()})


class HashCase(unittest.TestCase):
    """散列与校验：新散列走 argon2id，老散列照样能验，损坏数据不抛异常。"""

    def test_new_hash_is_argon2id_and_verifies(self):
        self.assertTrue(security.argon2_available(), "本机应装了 argon2-cffi")
        stored = security.hash_password("secret")
        self.assertTrue(stored.startswith(security.ARGON2_PREFIX), f"新散列应是 argon2id：{stored[:24]}")
        self.assertTrue(security.verify_hash(stored, "secret"))
        self.assertFalse(security.verify_hash(stored, "wrong"))
        self.assertFalse(security.needs_rehash(stored), "刚生成的新散列不该需要升级")

    def test_legacy_pbkdf2_still_verifies_and_needs_rehash(self):
        stored = _legacy_pbkdf2("secret")
        self.assertTrue(security.verify_hash(stored, "secret"))
        self.assertFalse(security.verify_hash(stored, "wrong"))
        self.assertTrue(security.needs_rehash(stored), "老 PBKDF2 散列应被标记为需要升级")

    def test_empty_hash_means_no_password(self):
        self.assertTrue(security.verify_hash("", "随便"))
        self.assertFalse(security.needs_rehash(""))

    def test_broken_hash_is_refused_without_raising(self):
        for broken in ("{ not json", '{"salt": "zz", "hash": "xx"}', "$argon2id$garbage"):
            with self.subTest(stored=broken):
                self.assertFalse(security.verify_hash(broken, "secret"))
                self.assertFalse(security.needs_rehash(broken))


class LazyUpgradeCase(IsolatedCase):
    """登录成功后顺手升级老散列（惰性 rehash）。"""

    def test_verify_upgrades_legacy_hash(self):
        service = UserService(self.session)
        user = service.create("升级用例", "")
        self.assertIsNotNone(user)
        user.password_hash = _legacy_pbkdf2("secret")
        self.session.flush()

        self.assertFalse(service.verify(user, "wrong"), "错口令不该通过")
        self.assertTrue(service.verify(user, "secret"))
        self.assertTrue(
            user.password_hash.startswith(security.ARGON2_PREFIX),
            f"校验成功后应升级成 argon2id：{user.password_hash[:24]}",
        )
        # 升级后的散列仍能验，且不再是「需要升级」状态
        self.assertTrue(security.verify_hash(user.password_hash, "secret"))
        self.assertFalse(security.needs_rehash(user.password_hash))

    def test_fresh_hash_is_not_rewritten(self):
        service = UserService(self.session)
        user = service.create("新口令用例", "")
        service.set_password(user, "secret")
        before = user.password_hash
        self.assertTrue(service.verify(user, "secret"))
        self.assertEqual(user.password_hash, before, "已经是 argon2 就不该重写")


if __name__ == "__main__":
    unittest.main()
