"""
Read-your-writes cache (read-side).

The Read API checks this cache before PostgreSQL on GET /orders/{id}. On a
cache miss it falls through to PostgreSQL, then repopulates the cache so the
next reader hits (read-through / lazy load).

Duplicated across write-api/ and read-api/.
"""

import os
import json
import logging

from redis import Redis
from redis.exceptions import RedisError

REDIS_URL  = os.getenv("REDIS_CACHE_URL", "redis://localhost:6380")
KEY_PREFIX = "order:"
TTL_SEC    = int(os.getenv("ORDER_CACHE_TTL_SEC", "60"))

_log = logging.getLogger("ryw_cache")


class OrderCache:
    def __init__(self):
        self._r = Redis.from_url(REDIS_URL)

    def _key(self, order_id: str) -> str:
        return f"{KEY_PREFIX}{order_id}"

    def get_order(self, order_id: str):
        try:
            raw = self._r.get(self._key(order_id))
            return json.loads(raw) if raw else None
        except RedisError as e:
            _log.warning("cache.get_order failed for %s: %s", order_id, e)
            return None

    def set_order(self, order_id: str, doc: dict) -> None:
        try:
            self._r.set(self._key(order_id), json.dumps(doc, default=str), ex=TTL_SEC)
        except RedisError as e:
            _log.warning("cache.set_order failed for %s: %s", order_id, e)
