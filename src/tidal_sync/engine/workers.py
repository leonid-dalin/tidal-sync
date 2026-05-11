"""
Concurrency wrappers, thread pool management, and state tracking.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from loguru import logger
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

console = Console()
MAX_CONCURRENCY = 10


@dataclass
class ImportStats:
    """
    Async-safe counter for the final terminal summary

    Tracks the number of skipped, failed, and added items during an
    import session across multiple concurrent threads.
    """
    skipped: int = 0
    failed: int = 0
    added: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add_skipped(self) -> None:
        async with self.lock: self.skipped += 1

    async def add_failed(self) -> None:
        async with self.lock: self.failed += 1

    async def add_added(self, count: int = 1) -> None:
        async with self.lock: self.added += count


async def handle_match_result_async(
        matched_id: str | None,
        item_type: str,
        item_name: str,
        artist_name: str,
        source_file: str,
        dest_name: str,
        existing_ids: set[str],
        stats: ImportStats,
        add_method: Callable[[str], Awaitable[Any]] | None = None,
        ids_to_add: list[str] | None = None
) -> None:
    """
    Safely logs and updates statistics for a matched item using an async lock

    Prevents race conditions when multiple threads attempt to add tracks to
    the same playlist or update the global counter simultaneously.
    """
    if matched_id:
        is_new = False

        async with stats.lock:
            if matched_id not in existing_ids:
                existing_ids.add(matched_id)
                if ids_to_add is not None:
                    ids_to_add.append(matched_id)
                is_new = True

        if is_new:
            if add_method:
                await add_method(matched_id)
            logger.bind(audit=True).debug("Item Staged", type=item_type, name=item_name, dest=dest_name)
        else:
            await stats.add_skipped()
            logger.bind(audit=True).info("Skipped (Duplicate)", type=item_type, name=item_name, artist=artist_name, dest=dest_name)
    else:
        await stats.add_failed()
        logger.bind(audit=True).warning("Failed (Not Found)", type=item_type, name=item_name, artist=artist_name, source=source_file)


async def run_matching_tasks_async(task_desc: str, items: list[Any], match_func: Callable[[Any], Awaitable[Any]]) -> None:
    """
    Runs matching functions concurrently while displaying a rich progress bar.

    This abstracts away the ThreadPoolExecutor boilerplate and ensures all
    futures are explicitly resolved to surface any swallowed exceptions.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
    ) as progress:
        task = progress.add_task(task_desc, total=len(items))

        async def _bounded_task(item: Any) -> None:
            async with semaphore:
                await match_func(item)
                progress.advance(task)

        async with asyncio.TaskGroup() as tg:
            for item in items:
                tg.create_task(_bounded_task(item))


async def run_headless_tasks_async(items: list[Any], task_func: Callable[[Any], Awaitable[Any]]) -> None:
    """
    A headless async wrapper for tasks that don't need a progress bar
    (e.g., executing background deletes during a clear operation).
    """
    if not items:
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _bounded_task(item: Any) -> None:
        async with semaphore:
            await task_func(item)

    async with asyncio.TaskGroup() as tg:
        for item in items:
            tg.create_task(_bounded_task(item))