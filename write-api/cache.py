"""
Read-your-writes cache (write-side).

The Write API calls into this module after every successful command. On a
successful place_order we SET the full order doc; on confirm-payment / ship /
cancel we read-modify-write the cached doc. The Read API checks this cache
before PostgreSQL, closing the projection-lag window for point lookups.

Cache is never authoritative — MongoDB is still the source of truth, PostgreSQL
is still the canonical read model. Redis failures must not break writes.
"""

import os
import json
import logging

from redis import Redis
from redis.exceptions import RedisError

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379")
KEY_PREFIX = "order:"
TTL_SEC    = int(os.getenv("ORDER_CACHE_TTL_SEC", "60"))

_log = logging.getLogger("ryw_cache")


class OrderCache:
    def __init__(self):
        self._r = Redis.from_url(REDIS_URL)

    def _key(self, order_id: str) -> str:
        return f"{KEY_PREFIX}{order_id}"

    def set_order(self, order_id: str, doc: dict) -> None:
        try:
            self._r.set(self._key(order_id), json.dumps(doc), ex=TTL_SEC)
        except RedisError as e:
            _log.warning("cache.set_order failed for %s: %s", order_id, e)

    def patch_order(self, order_id: str, **fields) -> None:
        try:
            raw = self._r.get(self._key(order_id))
            if raw is None:
                return  # cache miss — the read path will warm it via read-through
            doc = json.loads(raw)
            doc.update(fields)
            self._r.set(self._key(order_id), json.dumps(doc), ex=TTL_SEC)
        except RedisError as e:
            _log.warning("cache.patch_order failed for %s: %s", order_id, e)

    def invalidate(self, order_id: str) -> None:
        try:
            self._r.delete(self._key(order_id))
        except RedisError as e:
            _log.warning("cache.invalidate failed for %s: %s", order_id, e)
