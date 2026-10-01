"""口令散列工具：用户口令（进入口令/隐藏数据解锁）的散列与校验。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from loguru import logger

ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """把口令散列成可存储的 JSON 字符串（PBKDF2-HMAC-SHA256）。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return json.dumps(
        {"iter": ITERATIONS, "salt": salt.hex(), "hash": digest.hex()}, ensure_ascii=False
    )


def verify_hash(stored: str, password: str) -> bool:
    """校验口令散列；无散列时视为不校验。"""
    if not stored:
        return True
    try:
        payload = json.loads(stored)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            bytes.fromhex(payload["salt"]),
            int(payload["iter"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("口令数据损坏：{}", exc)
        return False
    return hmac.compare_digest(digest.hex(), payload["hash"])


__all__ = ["ITERATIONS", "hash_password", "verify_hash"]
