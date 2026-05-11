# tidal-sync: A high-performance tool for backing up and cloning Tidal libraries.
# Copyright (C) 2026 Leonid Dalin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 or later of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: infoLeonid@protonMail.com
"""
Asynchronous concurrency orchestration and state tracking.

Manages high-performance task execution using modern `asyncio.TaskGroup`
and `asyncio.Semaphore` implementations to bound network throughput. It
provides async-safe statistical counters and ensures race-condition-free
state management during bulk track matching and uploading.
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
        ids_to_add: list[str] | None = None,
        failure_reason: str = "Not Found on Tidal"
) -> None:
    """
    Processes and logs the outcome of a metadata matching operation.

    Updates the global session statistics using an asynchronous lock to
    prevent race conditions. It ensures network actions (like adding a track)
    execute outside the lock to maintain true concurrency across the worker pool.

    Args:
        matched_id (str | None): The Tidal database ID if a match was found.
        item_type (str): The category of the item (e.g., "Track", "Album").
        item_name (str): The primary title of the item.
        artist_name (str): The primary artist associated with the item.
        source_file (str): The filename where the metadata originated.
        dest_name (str): The target playlist or category in the user's library.
        existing_ids (set[str]): A shared set of IDs already present in the destination.
        stats (ImportStats): The shared counter tracking session outcomes.
        add_method (Callable | None): The network function to execute if the item is new.
        ids_to_add (list[str] | None): A batch array to append the ID to (for chunked uploads).
        failure_reason (str): The reason logged if the match failed. Defaults to "Not Found on Tidal".
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
                try:
                    await add_method(matched_id)
                    await stats.add_added()
                except Exception as e:
                    # Catch region-locks/404s for single items (Albums/Artists)
                    # so they don't crash the entire TaskGroup.
                    await stats.add_failed()
                    logger.bind(audit=True).error(
                        "Item Add Failed (Region Locked or Removed)",
                        type=item_type,
                        name=item_name,
                        artist=artist_name,
                        error=str(e)
                    )
                    return
            logger.bind(audit=True).debug(
                "Item Staged",
                type=item_type,
                name=item_name,
                dest=dest_name
            )
        else:
            await stats.add_skipped()
            logger.bind(audit=True).info(
                "Skipped (Duplicate)",
                type=item_type,
                name=item_name,
                artist=artist_name,
                dest=dest_name
            )
    else:
        await stats.add_failed()
        logger.bind(audit=True).warning(
            "Failed to Match",
            type=item_type,
            name=item_name,
            artist=artist_name,
            source=source_file,
            reason=failure_reason
        )

async def run_matching_tasks_async(task_desc: str, items: list[Any], match_func: Callable[[Any], Awaitable[Any]]) -> None:
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