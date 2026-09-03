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
"""CLI sub-app for managing filter-list subscriptions.

Layer rule (gate 3): the CLI decides whether to ask the operator;
``filterlist_apply`` decides what the sets are and what prune means.
This module never recomputes ``to_block`` or ``unlisted`` from the
raw subscriptions: it forwards the operator's flags to ``plan_apply``
and prints the result. The unblock prompt is wired here because the
CLI owns the "ask the operator" decision, but the list of unblock
candidates comes from the engine.

The CLI is the only layer that issues Tidal writes for an apply.
``plan_apply`` is pure and ``execute_apply`` is the single mutation
point; both run inside one ``asyncio.run`` call so the confirmation
rail sits between them and a declined prompt issues zero block
writes.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, NoReturn

import typer

from .auth import get_session
from .cli_prompts import prompt_unblock
from .cli_shared import BLOCK_RAIL_THRESHOLD, console
from .domain.exceptions import TidalAuthenticationError, TidalSyncError
from .domain.results import UploadOutcome
from .engine.filterlist import FormatError, detect_format
from .engine.filterlist_apply import MAX_APPLY_IDS, ApplyPlan, _now_iso, execute_apply, plan_apply
from .engine.filterlist_fetch import FetchError, fetch_source
from .engine.filterlist_store import (
    StoreError,
    Subscription,
    add_subscription,
    cache_path,
    load_subscriptions,
    remove_subscription,
    save_subscriptions,
    store_index_path,
)

blocklist_app = typer.Typer(
    name="blocklist",
    help="Manage filter-list subscriptions and apply them to your Tidal blocklist.",
    no_args_is_help=True,
)


def report_store_error(exc: StoreError) -> NoReturn:
    """Print the standard unreadable-store message and exit 1."""
    console.print(f"[bold red]Subscription store unreadable:[/bold red] {exc}")
    console.print(
        f"  [dim]store path: {store_index_path()}[/dim]\n"
        "  [dim]fix or delete subscriptions.json, then retry.[/dim]"
    )
    raise typer.Exit(1) from exc


def report_capped(to_block_count: int) -> NoReturn:
    console.print(
        f"[bold red]Refused:[/bold red] to_block has {to_block_count} ids, "
        f"exceeds MAX_APPLY_IDS={MAX_APPLY_IDS}; aborting"
    )
    raise typer.Exit(1)


def _format_table(rows: list[Subscription]) -> None:
    if not rows:
        console.print("[yellow]No subscriptions.[/yellow]")
        return
    for sub in rows:
        fetched = sub.last_fetched or "never"
        console.print(
            f"  [bold cyan]{sub.name}[/bold cyan] "
            f"({sub.format}) "
            f"[dim]last_fetched={fetched} "
            f"last_count={sub.last_count}"
            + (f" last_error={sub.last_error}" if sub.last_error else "")
            + "[/dim]"
        )
        console.print(f"    [dim]{sub.source}[/dim]")


def _print_plan(plan: ApplyPlan) -> None:
    """Print every set on an ``ApplyPlan`` in the order the CLI has always used.

    A module-level helper so the print loops are not duplicated
    between the dry-run branch and the write branch.
    """
    for tid, name in plan.to_block:
        console.print(f"  [green]to_block {tid}[/green] [dim]({name})[/dim]")
    for tid, name in plan.already_blocked:
        console.print(f"  [cyan]already_blocked {tid}[/cyan] [dim]({name})[/dim]")
    for tid, name in plan.unlisted:
        console.print(f"  [yellow]unlisted {tid}[/yellow] [dim]({name})[/dim]")
    for sub_name, err in plan.errors:
        console.print(f"  [red]error {sub_name}: {err}[/red]")


@blocklist_app.command(name="add")
def add(
    name: Annotated[str, typer.Argument(help="Subscription name")],
    source: Annotated[str, typer.Argument(help="HTTPS URL or local path")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name (accepted for parity)")
    ] = "default",
) -> None:
    """Subscribe to a filter list, validating the format before persisting.

    The fetch here is the same one ``update`` performs: the goal is to
    reject an unsupported extension at add time so a bad subscription
    never reaches apply, and to record ``last_count`` and
    ``last_fetched`` plus the cache file so ``show`` reflects the
    subscription truthfully. Reusing ``fetch_source`` keeps the four
    fetch caps (HTTPS only, 1 MiB cap, content-type allowlist, timeout)
    on the validation path too.
    """
    try:
        fmt = detect_format(source)
        count = fetch_source(source, fmt, cache_path(name, fmt))
    except FormatError as exc:
        console.print(f"[bold red]Refused subscription:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    except FetchError as exc:
        console.print(f"[bold red]Refused subscription:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[bold red]Refused subscription:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    sub = Subscription(
        name=name,
        source=source,
        format=fmt,
        last_fetched=_now_iso(),
        last_count=count,
        last_error=None,
    )
    add_subscription(sub)
    console.print(f"[green]Subscribed {name} ({fmt}).[/green]")


@blocklist_app.command(name="remove")
def remove(
    name: Annotated[str, typer.Argument(help="Subscription name to remove")],
) -> None:
    """Drop a subscription by name."""
    if not remove_subscription(name):
        console.print(f"[bold red]No such subscription:[/bold red] {name}")
        raise typer.Exit(1)
    console.print(f"[green]Removed {name}.[/green]")


@blocklist_app.command(name="update")
def update(
    name: Annotated[
        str | None,
        typer.Argument(help="Name to update, or omit for every subscription"),
    ] = None,
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name (accepted for parity)")
    ] = "default",
) -> None:
    """Refetch one or every subscription and record per-subscription errors."""
    try:
        all_subs = load_subscriptions()
    except StoreError as exc:
        report_store_error(exc)
    if name is not None:
        subs = [s for s in all_subs if s.name == name]
        if not subs:
            console.print(f"[bold red]No such subscription:[/bold red] {name}")
            raise typer.Exit(1)
    else:
        subs = all_subs

    if not subs:
        console.print("[yellow]No subscriptions to update.[/yellow]")
        return

    had_errors = False
    for sub in subs:
        dest = cache_path(sub.name, sub.format)
        try:
            count = fetch_source(sub.source, sub.format, dest)
            sub.last_fetched = _now_iso()
            sub.last_count = count
            sub.last_error = None
        except FetchError as exc:
            sub.last_error = str(exc)
            had_errors = True
        console.print(
            f"  [cyan]{sub.name}[/cyan] last_count={sub.last_count}"
            + (f" [red]error={sub.last_error}[/red]" if sub.last_error else "")
        )

    save_subscriptions(all_subs)

    if had_errors:
        raise typer.Exit(1)


@blocklist_app.command(name="show")
def show() -> None:
    """Print every subscription with its source, format and last fetch state."""
    try:
        rows = load_subscriptions()
    except StoreError as exc:
        report_store_error(exc)
    _format_table(rows)


async def _run_apply(
    *,
    session,
    profile: str,
    subs: list[Subscription],
    dry_run: bool,
    prune: bool,
    force: bool,
) -> None:
    """Single async driver for ``blocklist apply``.

    Computes the plan, prints it, gates on the rail, and only then
    hands the plan to ``execute_apply``. The rail is evaluated
    after the plan is built but before any Tidal write, so a
    declined confirmation issues zero block writes.
    """
    plan = await plan_apply(session, subs)
    _print_plan(plan)

    if dry_run:
        return

    if plan.to_block and not force and len(plan.to_block) > BLOCK_RAIL_THRESHOLD:
        typed = typer.prompt(f"Type '{profile}' to confirm blocking {len(plan.to_block)} artists")
        if typed != profile:
            console.print("[red]Confirmation did not match. Aborting.[/red]")
            raise typer.Exit(1)

    if prune:
        unblock_ids = [tid for tid, _name in plan.unlisted]
    else:
        unblock_ids = prompt_unblock(plan.unlisted, force=force) if plan.unlisted else []

    outcome = await execute_apply(session, plan, unblock_ids=unblock_ids)

    if outcome.capped:
        report_capped(len(plan.to_block))

    failed = _report_outcome(outcome.blocked, "Blocked artist", "block failed")
    failed += _report_outcome(outcome.unblocked, "unblock", "unblock failed")

    if failed or plan.errors:
        raise typer.Exit(1)


def _report_outcome(outcome: UploadOutcome | None, applied: str, rejected: str) -> int:
    """Single reporter for both directions so block and unblock output cannot diverge."""
    if outcome is None:
        return 0
    for tid in outcome.applied:
        console.print(f"  [green]{applied} {tid}[/green]")
    for tid in outcome.rejected:
        console.print(f"  [red]{rejected} {tid}[/red]")
    return len(outcome.rejected)


@blocklist_app.command(name="apply")
def apply(
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Which account profile to apply on")
    ] = "default",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Compute the plan only; no Tidal writes")
    ] = False,
    prune: Annotated[
        bool, typer.Option("--prune", help="Also unblock artists named by no subscription")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Skip the rail and the unblock prompt")
    ] = False,
) -> None:
    """Apply the union of every subscription to the named profile.

    Fetches stale subscriptions, parses cached ones, partitions against
    the live blocklist, and (unless ``--dry-run``) blocks the missing
    set. ``--prune`` extends the destructive reach to artists on the
    live blocklist named by no subscription; the unblock prompt is the
    CLI's interactive path and is skipped under ``--force``.

    The engine decides what the sets are and what prune means; this
    module only forwards flags and prints the result.
    """
    try:
        subs = load_subscriptions()
    except StoreError as exc:
        report_store_error(exc)
    if not subs:
        console.print("[yellow]No subscriptions. Use 'tidal-sync blocklist add' first.[/yellow]")
        return

    try:
        session = get_session(profile)
        asyncio.run(
            _run_apply(
                session=session,
                profile=profile,
                subs=subs,
                dry_run=dry_run,
                prune=prune,
                force=force,
            )
        )
    except TidalAuthenticationError as exc:
        console.print(f"[bold red]Authentication Failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    except TidalSyncError as exc:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {exc}")
        raise typer.Exit(1) from exc
