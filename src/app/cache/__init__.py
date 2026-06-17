"""Cache key builders for fastapi-cache2.

Each public function in this module follows the ``KeyBuilder`` protocol
defined by ``fastapi_cache.types.KeyBuilder``:

    (func, namespace: str = "", *, request, response, args, kwargs) -> str

Routes pass a function reference to ``@cache(namespace=..., key_builder=...)``
and the builder returns the cache key.
"""

from app.cache.keys import thread_list_key_builder

__all__ = ["thread_list_key_builder"]
