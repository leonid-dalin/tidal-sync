"""
Asynchronous concurrency orchestration and state tracking.

Manages high-performance task execution using modern `asyncio.TaskGroup`
and `asyncio.Semaphore` implementations to bound network throughput. It
provides async-safe statistical counters and ensures race-condition-free
state management during bulk track matching and uploading.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

console = Console()
MAX_CONCURRENCY = 10


@dataclass
class ImportStats:
    """
    Async-safe aggregator for import session statistics.

    Tracks the total number of items added, skipped, or failed across multiple
    concurrent tasks. Uses an internal lock to ensure atomic updates to counters
    and the shared 'existing_ids' set.
    """

    skipped: int = 0
    failed: int = 0
    added: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add_skipped(self) -> None:
        async with self.lock:
            self.skipped += 1

    async def add_failed(self) -> None:
        async with self.lock:
            self.failed += 1

    async def add_added(self, count: int = 1) -> None:
        async with self.lock:
            self.added += count


async def run_matching_tasks_async(
    task_desc: str, items: list[Any], match_func: Callable[[Any], Awaitable[Any]]
) -> None:
    """
    Executes concurrent asynchronous tasks while updating a Rich progress bar.

    Args:
        task_desc (str): The text displayed next to the loading spinner.
        items (list[Any]): The payload objects to process.
        match_func (Callable[[Any], Awaitable[Any]]): The async worker function.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(task_desc, total=len(items))

        async def _bounded_task(item: Any) -> None:
            async with semaphore:
                await match_func(item)
                progress.advance(task)

        async with asyncio.TaskGroup() as tg:
            for item in items:
                tg.create_task(_bounded_task(item))


async def run_headless_tasks_async(
    items: list[Any], task_func: Callable[[Any], Awaitable[Any]]
) -> None:
    """
    Executes a list of asynchronous tasks concurrently without terminal UI feedback.

    Restricts total concurrency using an asyncio.Semaphore to prevent local memory
    exhaustion or socket starvation when processing thousands of items.

    Args:
        items (list[Any]): The payload objects to process.
        task_func (Callable[[Any], Awaitable[Any]]): The async worker function.
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
