"""
Network orchestration and rate-limiting for the Tidal API.

Provides a centralised gatekeeper to manage API backoff across concurrent
asynchronous tasks. It intercepts HTTP 429 (Too Many Requests) and HTTP 403
(Abuse Detected) errors, pausing all workers to prevent account suspensions.
"""

import asyncio
import inspect
import random
import time
from collections.abc import Callable
from typing import Any

import requests
import tidalapi
from loguru import logger
from tidalapi.exceptions import TooManyRequests

from ..domain.exceptions import (
    TidalRateLimitError,
    TidalTransientError,
)

# Upload batch size and pagination page size. Both are transport limits, so
# they belong with the transport rather than in the domain package.
CHUNK_SIZE = 50


class GlobalTidalGate:
    """
    Process-global thread-safe pacer to bound network throughput.

    Tracks the active backoff window. When one worker receives a rate-limit
    response, it updates the shared clock. Sibling workers check this clock
    during their pre-flight checks and sleep rather than opening new connections
    into an active throttle.
    """

    def __init__(self):
        self.backoff_until: float = 0.0
        self.lock = asyncio.Lock()

    async def pre_flight_check(self):
        """Forces workers to pause if a sibling thread triggered a backoff"""
        async with self.lock:
            remaining = max(0.0, self.backoff_until - time.monotonic())

        # Sleeping outside the lock is deliberate: holding it across the sleep
        # blocks trigger_backoff for the whole window, so a second throttle
        # signal cannot extend the window while any worker is asleep.
        if remaining:
            logger.warning("Global Gate active. Sleeping for {remaining:.1f}s", remaining=remaining)
            await asyncio.sleep(remaining)

    async def trigger_backoff(self, seconds: float, reason: str = "Rate Limit 429") -> None:
        """Called by a worker that gets a 429 or 403"""
        async with self.lock:
            new_time = time.monotonic() + seconds
            if new_time > self.backoff_until:  # only extend, never shorten
                self.backoff_until = new_time
                logger.error(
                    "Engaging global throttle for {seconds}s: {reason}",
                    seconds=seconds,
                    reason=reason,
                )


GLOBAL_GATE = GlobalTidalGate()


_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RATE_LIMIT_DEFAULT = 60.0
_ABUSE_LOCK = 1800.0


def classify_error(error: BaseException) -> str | None:
    """Classifies an API failure as 'rate-limit', 'abuse', 'transient', or None.

    The status is read off the response object, never from str(error), so an
    error mentioning a track with '429' in its id is not mistaken for a
    throttle. A response carrying an explicit status is never retried unless
    that status is retryable: HTTPError subclasses RequestException, so a bare
    RequestException check after the status tests would retry every 4xx.
    """
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)

    if status == 429 or isinstance(error, TooManyRequests):
        return "rate-limit"

    if status == 403:
        message = str(error).lower()
        if "abuse" in message or "11003" in message:
            return "abuse"
        return None

    if status in _RETRYABLE_STATUS:
        return "transient"

    if status is not None:
        # An explicit non-retryable status: retrying a 400 or 401 cannot
        # succeed, and costs five attempts plus backoff each time.
        return None

    if isinstance(error, requests.exceptions.RequestException):
        # No status means no response reached us: connection reset, timeout,
        # dropped chunk. Those are worth another attempt.
        return "transient"

    return None


async def execute_network(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    **kwargs: Any,
) -> Any:
    """
    Executes a Tidal API call behind the global gate, retrying transient failures.

    Wraps the synchronous API execution in an asyncio thread to prevent
    blocking the main event loop. Retries apply only to rate limits, 5xx,
    and connection-level failures; other 4xx responses fail fast.

    Args:
        func (Callable): The synchronous Tidal API method to execute.
        *args: Positional arguments for the API method.
        max_retries (int): Attempts before giving up.
        base_delay (float): First backoff step, doubled each attempt.
        max_delay (float): Ceiling for a single backoff.
        **kwargs: Keyword arguments for the API method.

    Returns:
        Any: The parsed response from the Tidal API.

    Raises:
        TidalRateLimitError: A throttle outlived the retry budget.
        TidalTransientError: Another retryable failure outlived the budget.

    Example:
        >>> result = await execute_network(session.search, "artist name")
    """
    last_error: BaseException | None = None
    kind: str | None = None

    for attempt in range(max_retries):
        await GLOBAL_GATE.pre_flight_check()
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - reclassified below
            kind = classify_error(e)
            if kind is None:
                raise
            last_error = e

            if kind == "abuse":
                # TooManyRequests defaults retry_after to -1, not to absent,
                # so a numeric fallback would shrink a 30 minute account lock
                # to one second. The abuse lock bypasses max_delay too.
                await GLOBAL_GATE.trigger_backoff(_ABUSE_LOCK, f"Abuse lock: {e}")
            elif kind == "rate-limit":
                retry_after = getattr(e, "retry_after", 0.0) or 0.0
                if retry_after <= 0:
                    retry_after = _RATE_LIMIT_DEFAULT
                await GLOBAL_GATE.trigger_backoff(min(retry_after, max_delay), f"Rate limit: {e}")
            else:
                await GLOBAL_GATE.trigger_backoff(base_delay, f"Transient: {e}")

            if attempt + 1 < max_retries:
                # Jitter, so workers stop waking in lockstep and re-triggering
                # the throttle they just hit.
                delay = min(base_delay * (2**attempt), max_delay)
                await asyncio.sleep(delay * (0.5 + random.random() / 2))

    if kind in ("rate-limit", "abuse"):
        raise TidalRateLimitError(
            f"Rate limit persisted after {max_retries} attempts"
        ) from last_error
    raise TidalTransientError(
        f"Transient failure persisted after {max_retries} attempts: {last_error}"
    ) from last_error


async def fetch_all_async(api_method: Any, **kwargs: Any) -> list[Any]:
    """
    Exhaustively fetches paginated items from a Tidal API endpoint asynchronously.

    Tidal limits responses to 50 items and occasionally drops region-locked
    tracks from the internal count. This manual pagination bypasses those limits
    by advancing the offset until the server returns an empty list.

    Args:
        api_method (Any): The Tidal API function to call (e.g., session.user.playlists).
        **kwargs: Additional parameters passed directly to the API endpoint.

    Returns:
        list[Any]: A complete, unpaginated list of all items.
    """
    return await execute_network(_fetch_all_sync, api_method, **kwargs)


def _accepts_pagination(api_method: Any) -> bool:
    """Reports whether the callable's signature takes limit and offset.

    Deciding this from the signature rather than by catching TypeError means
    a TypeError raised inside the API call is never mistaken for an endpoint
    that simply does not paginate.
    """
    try:
        parameters = inspect.signature(api_method).parameters
    except (TypeError, ValueError):
        return False
    return "limit" in parameters and "offset" in parameters


def _fetch_all_sync(api_method: Any, **kwargs: Any) -> list[Any]:
    """
    The synchronous core logic for exhaustive API pagination.

    Iterates through Tidal's paginated responses by incrementing the offset
    parameter until the server returns an empty set or a duplicate page. This
    function is typically wrapped by `fetch_all_async` to run in a background thread.

    Args:
        api_method (Any): The synchronous Tidal API method to call.
        **kwargs: Parameters such as 'limit' or 'offset' passed to the API.

    Returns:
        list[Any]: A consolidated list of all recovered items.
    """
    items: list[Any] = []
    offset = 0
    limit = 50
    last_chunk_ids: list[Any] = []

    if not _accepts_pagination(api_method):
        res = api_method(**kwargs)
        return res if isinstance(res, list) else list(res)

    while True:
        chunk = api_method(limit=limit, offset=offset, **kwargs)
        if not chunk:
            break

        current_chunk_ids = [getattr(item, "id", id(item)) for item in chunk]

        # Infinite loop guard: detects if the API ignores the offset and repeats pages
        if offset > 0 and current_chunk_ids == last_chunk_ids:
            break

        items.extend(chunk)
        last_chunk_ids = current_chunk_ids
        offset += limit

    return items


def fetch_blocked_artists(session: tidalapi.Session) -> list[Any]:
    """
    Fetches the user's blocked or muted artists via an internal API endpoint.

    This function manually maps requests to the user blocklist endpoint.
    It handles pagination to ensure all blocked entries are recovered.

    Args:
        session (tidalapi.Session): The active Tidal session with a valid user ID.

    Returns:
        list[Any]: A list of artist objects recovered from the blocklist.
    """
    user = session.user
    if not user or not getattr(user, "id", None):
        return []

    endpoint = f"users/{user.id}/blocks/artists"

    items = []
    offset = 0
    limit = 50

    while True:
        params = {"limit": limit, "offset": offset}
        try:
            chunk = session.request.map_request(endpoint, params=params, parse=session.parse_artist)
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
