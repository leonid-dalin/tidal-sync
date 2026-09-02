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
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

import typer

from .cli_prompts import prompt_unblock
from .domain.exceptions import TidalAuthenticationError, TidalSyncError
from .engine.curation import block_artists, unblock_artists
from .engine.filterlist import FormatError, parse_filter_list
from .engine.filterlist_apply import plan_apply
from .engine.filterlist_fetch import FetchError, fetch_source
from .engine.filterlist_store import (
    Subscription,
    add_subscription,
    cache_path,
    load_subscriptions,
    remove_subscription,
)

blocklist_app = typer.Typer(
    name="blocklist",
    help="Manage filter-list subscriptions and apply them to your Tidal blocklist.",
    no_args_is_help=True,
)


_SUPPORTED_FORMATS: tuple[str, ...] = ("txt", "csv", "json")


def _detect_format(source: str) -> str:
    """Return the lowercase extension of ``source``, with no leading dot."""
    dot = source.rfind(".")
    if dot == -1 or dot == len(source) - 1:
        raise FormatError(f"cannot detect format: source {source!r} has no extension")
    return source[dot + 1 :].lower()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _format_table(rows: list[Subscription]) -> None:
    from .cli import console

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


@blocklist_app.command(name="add")
def add(
    name: Annotated[str, typer.Argument(help="Subscription name")],
    source: Annotated[str, typer.Argument(help="HTTPS URL or local path")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name (accepted for parity)")
    ] = "default",
) -> None:
    """Subscribe to a filter list, validating the format before persisting.

    The fetch here is throwaway: the goal is to reject an unsupported
    extension at add time so a bad subscription never reaches apply.
    """
    from .cli import console

    try:
        fmt = _detect_format(source)
        if fmt not in _SUPPORTED_FORMATS:
            raise FormatError(f"unsupported filter-list format: {fmt!r}")
        # Parse a minimal stub so the supported-formats branch is exercised
        # even when the operator points us at a remote URL we cannot reach
        # offline. Real fetch is performed by update or apply.
        parse_filter_list(b"# probe", fmt)
    except FormatError as exc:
        console.print(f"[bold red]Refused subscription:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    sub = Subscription(
        name=name,
        source=source,
        format=fmt,
        last_fetched=None,
        last_count=0,
        last_error=None,
    )
    add_subscription(sub)
    console.print(f"[green]Subscribed {name} ({fmt}).[/green]")


@blocklist_app.command(name="remove")
def remove(
    name: Annotated[str, typer.Argument(help="Subscription name to remove")],
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name (accepted for parity)")
    ] = "default",
) -> None:
    """Drop a subscription by name."""
    from .cli import console

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
    from .cli import console

    subs = load_subscriptions()
    if name is not None:
        subs = [s for s in subs if s.name]
        if not subs:
            console.print(f"[bold red]No such subscription:[/bold red] {name}")
            raise typer.Exit(1)

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
        add_subscription(sub)
        console.print(
            f"  [cyan]{sub.name}[/cyan] last_count={sub.last_count}"
            + (f" [red]error={sub.last_error}[/red]" if sub.last_error else "")
        )

    if had_errors:
        raise typer.Exit(1)


@blocklist_app.command(name="show")
def show(
    profile: Annotated[
        str, typer.Option("--profile", "-p", help="Profile name (accepted for parity)")
    ] = "default",
) -> None:
    """Print every subscription with its source, format and last fetch state."""
    _format_table(load_subscriptions())


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
    # Lazy imports to avoid the circular dependency with cli.py, which
    # imports this module to register the sub-app.
    from .cli import _BLOCK_RAIL_THRESHOLD, console, get_session

    subs = load_subscriptions()
    if not subs:
        console.print("[yellow]No subscriptions. Use 'tidal-sync blocklist add' first.[/yellow]")
        return

    try:
        session = get_session(profile)
        plan = asyncio.run(
            plan_apply(
                session,
                subs,
                dry_run=dry_run,
                prune=prune,
            )
        )
    except TidalAuthenticationError as exc:
        console.print(f"[bold red]Authentication Failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    except TidalSyncError as exc:
        console.print(f"[bold red]tidal-sync could not complete:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    for tid, name in plan.to_block:
        console.print(f"  [green]to_block {tid}[/green] [dim]({name})[/dim]")
    for tid, name in plan.already_blocked:
        console.print(f"  [cyan]already_blocked {tid}[/cyan] [dim]({name})[/dim]")
    for tid, name in plan.unlisted:
        console.print(f"  [yellow]unlisted {tid}[/yellow] [dim]({name})[/dim]")
    for sub_name, err in plan.errors:
        console.print(f"  [red]error {sub_name}: {err}[/red]")

    if dry_run:
        return

    if plan.to_block:
        if not force and len(plan.to_block) > _BLOCK_RAIL_THRESHOLD:
            typed = typer.prompt(
                f"Type '{profile}' to confirm blocking {len(plan.to_block)} artists"
            )
            if typed != profile:
                console.print("[red]Confirmation did not match. Aborting.[/red]")
                raise typer.Exit(1)
        to_block_ids = [tid for tid, _ in plan.to_block]
        outcome = asyncio.run(block_artists(session, to_block_ids))
        if outcome.rejected:
            for tid in outcome.rejected:
                console.print(f"  [red]block failed {tid}[/red]")
            raise typer.Exit(1)

    if plan.unlisted:
        # The CLI owns the "ask the operator" decision. prompt_unblock
        # already collapses every non-force path to []; the unblock
        # only runs for ids the operator picked.
        picked = prompt_unblock(plan.unlisted, force=force)
        if picked:
            unblock_outcome = asyncio.run(unblock_artists(session, picked))
            if unblock_outcome.rejected:
                for tid in unblock_outcome.rejected:
                    console.print(f"  [red]unblock failed {tid}[/red]")
                raise typer.Exit(1)

    if plan.errors:
        raise typer.Exit(1)
