import time
from functools import wraps
from typing import Any, Callable
from loguru import logger
from tidalapi.exceptions import TooManyRequests

def retry_on_429(max_retries: int = 5, backoff_factor: float = 1.5) -> Callable:
    """
    Handles Tidal API rate limits (HTTP 429) automatically via exponential backoff.

    If the API returns a 'retry_after' value, it waits exactly that long.
    Otherwise, it falls back to multiplying the delay by the backoff factor.

    Args:
        max_retries (int): Maximum number of retry attempts. Defaults to 5.
        backoff_factor (float): Multiplier for the delay time. Defaults to 1.5.

    Returns:
        Callable: The decorated function.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except TooManyRequests as e:
                    retry_after = getattr(e, 'retry_after', -1)
                    sleep_time = retry_after if retry_after > 0 else (backoff_factor ** retries)
                    logger.warning("Rate limited (429)", retry_after=sleep_time, attempt=retries+1)
                    time.sleep(sleep_time)
                    retries += 1
                except Exception as e:
                    # Fallback catch for generic 429s parsed as standard HTTP errors
                    if "429" in str(e):
                        time.sleep(backoff_factor ** retries)
                        retries += 1
                    else:
                        raise
            return func(*args, **kwargs)  # Final attempt
        return wrapper
    return decorator

def fetch_blocked_artists(session: tidalapi.Session) -> list[Any]:
    """
    Fetches blocked/muted artists directly via the session's internal request engine.
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


def fetch_all(api_method: Any, **kwargs: Any) -> list[Any]:
    """
    Exhaustively fetches paginated items from a Tidal API endpoint.

    Tidal limits responses to 50 items and occasionally drops region-locked
    tracks from the count. This helper bypasses those limits by manually
    advancing the offset until the server returns no new items.

    Args:
        api_method (Any): The Tidal API function to call (e.g., session.user.playlists).
        **kwargs: Additional arguments to pass to the API method.

    Returns:
        list[Any]: A complete list of all items from the endpoint.
    """
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