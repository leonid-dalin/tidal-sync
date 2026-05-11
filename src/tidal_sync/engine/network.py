import asyncio
import time
from functools import wraps
from typing import Any, Callable, Awaitable
from loguru import logger
from tidalapi.exceptions import TooManyRequests


class GlobalTidalGate:
    def __init__(self):
        self.backoff_until: float = 0.0
        self.lock = asyncio.Lock()

    async def pre_flight_check(self):
        """Forces workers to pause if a sibling thread triggered a backoff"""
        async with self.lock:
            now = time.time()
            if now < self.backoff_until:
                remaining = self.backoff_until - now
                logger.warning(f"Global Gate active. Sleeping for {remaining:.1f}s")
                await asyncio.sleep(remaining)

    async def trigger_backoff(self, seconds: float):
        """Called by a worker that gets a 429 or 403"""
        async with self.lock:
            new_time = time.time() + seconds
            if new_time > self.backoff_until:  # only extend, never shorten
                self.backoff_until = new_time
                logger.error(f"Engaging global throttle for {seconds}s")


GLOBAL_GATE = GlobalTidalGate()

async def execute_network(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Executes a synchronous Tidal API call safely behind the Global Gate"""
    retries = 0
    while retries < 5:
        await GLOBAL_GATE.pre_flight_check()
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except TooManyRequests as e:
            retry_after = getattr(e, 'retry_after', 60.0)
            if retry_after <= 0: retry_after = 60.0
            await GLOBAL_GATE.trigger_backoff(retry_after, "HTTP 429 Too Many Requests")
            retries += 1
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "too many requests" in error_str:
                await GLOBAL_GATE.trigger_backoff(60.0, "Generic 429 Too Many Requests")
                retries += 1
            elif "403" in error_str and ("abuse" in error_str or "11003" in error_str):
                await GLOBAL_GATE.trigger_backoff(1800.0, "HTTP 403 Abuse Detected (30m Lock)")
                retries += 1
            else:
                raise
    raise Exception(f"Max retries exceeded for {func.__name__} due to rate limiting.")

async def fetch_all_async(api_method: Any, **kwargs: Any) -> list[Any]:
    """
    Async wrapper for exhaustive pagination. Offloads the blocking
    API call to a background thread to keep the event loop free
    """
    return await execute_network(_fetch_all_sync, api_method, **kwargs)

def _fetch_all_sync(api_method: Any, **kwargs: Any) -> list[Any]:
    items = []
    offset = 0
    limit = 50
    last_chunk_ids = []

    while True:
        try:
            chunk = api_method(limit=limit, offset=offset, **kwargs)
        except TypeError:
            res = api_method(**kwargs)
            return res if isinstance(res, list) else list(res)

        if not chunk:
            break

        current_chunk_ids = [getattr(item, 'id', id(item)) for item in chunk]

        # Infinite loop guard: detects if the API ignores the offset and repeats pages
        if offset > 0 and current_chunk_ids == last_chunk_ids:
            break

        items.extend(chunk)
        last_chunk_ids = current_chunk_ids
        offset += limit

    return items

def fetch_blocked_artists(session: tidalapi.Session) -> list[Any]:
    """
    Fetches blocked/muted artists directly via the session's internal request engine
    """
    user = session.user
    if not user or not getattr(user, 'id', None):
        return []

    endpoint = f"users/{user.id}/blocks/artists"

    items = []
    offset = 0
    limit = 50

    while True:
        params = {"limit": limit, "offset": offset}
        try:
            chunk = session.request.map_request(
                endpoint,
                params=params,
                parse=session.parse_artist
            )
            if isinstance(chunk, dict) and "items" in chunk:
                chunk = [session.parse_artist(item.get("item", item)) for item in chunk["items"]]
        except Exception as e:
            logger.warning("Failed to fetch blocked artists", error=repr(e))
            break

        if not chunk:
            break

        items.extend(chunk)
        offset += limit

    return items