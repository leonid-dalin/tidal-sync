"""
Concurrency wrappers, thread pool management, and state tracking.
"""

import threading
import concurrent.futures
from dataclasses import dataclass
from typing import Any, Callable
from loguru import logger
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

console = Console()
MAX_WORKERS = 8


@dataclass
class ImportStats:
    """
    Thread-safe counter for the final terminal summary.

    Tracks the number of skipped, failed, and added items during an
    import session across multiple concurrent threads.
    """
    skipped: int = 0
    failed: int = 0
    added: int = 0
    lock: threading.Lock = threading.Lock()

    def add_skipped(self) -> None:
        with self.lock: self.skipped += 1

    def add_failed(self) -> None:
        with self.lock: self.failed += 1

    def add_added(self, count: int = 1) -> None:
        with self.lock: self.added += count


def handle_match_result(
        matched_id: str | None,
        item_type: str,
        item_name: str,
        artist_name: str,
        source_file: str,
        dest_name: str,
        existing_ids: set[str],
        stats: ImportStats,
        lock: threading.Lock,
        add_method: Callable[[str], Any] | None = None,
        ids_to_add: list[str] | None = None
) -> None:
    """
    Safely logs and updates statistics for a matched item using a thread lock.

    Prevents race conditions when multiple threads attempt to add tracks to
    the same playlist or update the global counter simultaneously.
    """
    with lock:
        if matched_id:
            if matched_id not in existing_ids:
                existing_ids.add(matched_id)
                if add_method:
                    add_method(matched_id)
                if ids_to_add is not None:
                    ids_to_add.append(matched_id)
                logger.bind(audit=True).debug("Item Staged", type=item_type, name=item_name, dest=dest_name)
            else:
                stats.add_skipped()
                logger.bind(audit=True).info("Skipped (Duplicate)", type=item_type, name=item_name, artist=artist_name,
                                             dest=dest_name)
        else:
            stats.add_failed()
            logger.bind(audit=True).warning("Failed (Not Found)", type=item_type, name=item_name, artist=artist_name,
                                            source=source_file)


def run_matching_tasks(task_desc: str, items: list[Any], match_func: Callable[[Any], Any]) -> None:
    """
    Runs matching functions concurrently while displaying a rich progress bar.

    This abstracts away the ThreadPoolExecutor boilerplate and ensures all
    futures are explicitly resolved to surface any swallowed exceptions.
    """
    with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
    ) as progress:
        task = progress.add_task(task_desc, total=len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(match_func, item) for item in items]
            for future in concurrent.futures.as_completed(futures):
                future.result()
                progress.advance(task)


def run_concurrent_tasks(items: list[Any], task_func: Callable[[Any], Any]) -> None:
    """
    A headless concurrency wrapper for tasks that don't need a progress bar
    (e.g., executing background deletes during a clear operation).
    """
    if not items: return
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(task_func, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            future.result()