from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from leadfinder.backfill import run_backfill
from leadfinder.classification import MessageContext, build_classifier
from leadfinder.config import get_settings
from leadfinder.db import Database
from leadfinder.discovery import run_discovery
from leadfinder.exporter import export_leads as write_leads_csv
from leadfinder.exporter import export_signals as write_signals_csv
from leadfinder.exporter import export_sources as write_sources_csv
from leadfinder.languages import normalize_language_filters
from leadfinder.models import (
    ChatSource,
    DiscoveryQuery,
    Lead,
    SearchProfileRecord,
    Signal,
    SourceSubscription,
)
from leadfinder.monitor import run_monitor
from leadfinder.notifications import (
    bot_api_from_settings,
    deliver_pending_notifications,
    poll_bot_updates,
)
from leadfinder.profiles import JETSKI_MIAMI, SearchProfileSpec
from leadfinder.reclassification import reclassify_pending_signals
from leadfinder.repository import spec_from_record, upsert_profile
from leadfinder.services import set_subscription_status
from leadfinder.sources import add_public_source, sync_account_dialogs
from leadfinder.telegram_gateway import (
    authorize_with_code,
    authorize_with_qr,
    authorized_account,
)

app = typer.Typer(no_args_is_help=True, help="Multi-source demand discovery and qualification")
console = Console()
DEFAULT_QR_OUTPUT = Path("data/telegram-login.png")
DEFAULT_SOURCES_EXPORT = Path("exports/sources.csv")
DEFAULT_LEADS_EXPORT = Path("exports/leads.csv")
DEFAULT_SIGNALS_EXPORT = Path("exports/signals.csv")


def _database() -> Database:
    return Database(get_settings())


def _classifier():
    return build_classifier(get_settings())


@app.command("init-db")
def init_db() -> None:
    """Create the local database schema."""
    database = _database()
    database.create_all()
    console.print("[green]Database schema is ready.[/green]")


@app.command("seed-profile")
def seed_profile(
    max_queries: int | None = typer.Option(None, min=1, max=500),
) -> None:
    """Create or update the default jetski Miami search profile."""
    settings = get_settings()
    database = _database()
    database.create_all()
    query_limit = max_queries or settings.discovery_max_queries
    with database.session() as session:
        profile = upsert_profile(session, JETSKI_MIAMI, query_limit=query_limit)
        profile_id = profile.id
    with database.session() as session:
        count = len(
            list(
                session.scalars(
                    select(DiscoveryQuery).where(
                        DiscoveryQuery.profile_id == profile_id,
                        DiscoveryQuery.active.is_(True),
                    )
                )
            )
        )
    console.print(f"[green]Profile {JETSKI_MIAMI.slug} is ready with {count} queries.[/green]")


@app.command("import-profile")
def import_profile(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    max_queries: int = typer.Option(120, min=1, max=500),
) -> None:
    """Create or update a generic search profile from a JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = ("slug", "name", "services", "locations", "intents", "classifier_prompt")
    missing = [name for name in required if not payload.get(name)]
    if missing:
        raise typer.BadParameter(f"Missing profile fields: {', '.join(missing)}")
    spec = SearchProfileSpec(
        slug=str(payload["slug"]),
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        services=tuple(payload["services"]),
        locations=tuple(payload["locations"]),
        intents=tuple(payload["intents"]),
        languages=normalize_language_filters(payload.get("languages", [])),
        negative_terms=tuple(payload.get("negative_terms", [])),
        positive_examples=tuple(payload.get("positive_examples", [])),
        classifier_prompt=str(payload["classifier_prompt"]),
    )
    database = _database()
    database.create_all()
    with database.session() as session:
        record = upsert_profile(session, spec, max_queries)
    console.print(f"[green]Profile {record.slug} imported.[/green]")


@app.command("list-queries")
def list_queries(
    profile: str = typer.Option("jetski-miami", help="Search profile slug"),
    limit: int = typer.Option(30, min=1, max=500),
) -> None:
    """Show generated Telegram search queries."""
    database = _database()
    database.create_all()
    with database.session() as session:
        record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile)
        )
        if record is None:
            raise typer.BadParameter("Unknown profile. Run seed-profile first.")
        queries = list(
            session.scalars(
                select(DiscoveryQuery)
                .where(DiscoveryQuery.profile_id == record.id)
                .order_by(DiscoveryQuery.id)
                .limit(limit)
            )
        )

    table = Table("ID", "Query", "Active", "Last run")
    for item in queries:
        table.add_row(
            str(item.id),
            item.query,
            "yes" if item.active else "no",
            str(item.last_run_at or "never"),
        )
    console.print(table)


@app.command("classify-text")
def classify_text(
    text: str = typer.Argument(..., help="Message text to classify"),
    profile: str = typer.Option("jetski-miami"),
) -> None:
    """Test the required Gemini classifier without contacting Telegram."""
    database = _database()
    database.create_all()
    with database.session() as session:
        record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile)
        )
        if record is None:
            raise typer.BadParameter("Unknown profile. Run seed-profile first.")
        spec = spec_from_record(record)

    result = _classifier().classify(spec, MessageContext(text=text, query=text))
    console.print(
        {
            "is_candidate": result.is_candidate,
            "final_score": round(result.final_score, 3),
            "keyword_score": round(result.keyword_score, 3),
            "embedding_score": result.embedding_score,
            "llm_score": result.llm_score,
            "reasons": result.reasons,
        }
    )


@app.command("auth-qr")
def auth_qr(
    output: Annotated[Path, typer.Option()] = DEFAULT_QR_OUTPUT,
    timeout: int = typer.Option(60, min=20, max=300),
) -> None:
    """Authorize the MTProto session by scanning a local QR code."""
    console.print(f"QR code will be written to: [cyan]{output.resolve()}[/cyan]")
    console.print("Scan it in Telegram: Settings → Devices → Link Desktop Device")
    try:
        account = asyncio.run(authorize_with_qr(get_settings(), output, timeout))
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Telegram authorized as {account}.[/green]")


@app.command("auth-code")
def auth_code(
    phone: str | None = typer.Option(
        None,
        help="Telegram phone number in international format; prompted locally if omitted",
    ),
) -> None:
    """Authorize the MTProto session with a phone number and one-time code."""
    if not sys.stdin.isatty():
        raise typer.BadParameter("Run auth-code in a local interactive terminal")
    local_phone = phone or typer.prompt("Telegram phone number, for example +13055550123")
    try:
        account = asyncio.run(
            authorize_with_code(
                get_settings(),
                local_phone,
                code_provider=lambda: typer.prompt("Telegram login code"),
                password_provider=lambda: typer.prompt(
                    "Telegram 2FA password",
                    hide_input=True,
                ),
            )
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Telegram authorized as {account}.[/green]")


@app.command("telegram-status")
def telegram_status() -> None:
    """Check whether the local session is authorized."""
    account = asyncio.run(authorized_account(get_settings()))
    if account is None:
        console.print("[yellow]Telegram session is not authorized.[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Telegram session is authorized as {account}.[/green]")


@app.command("discover")
def discover(
    profile: str = typer.Option("jetski-miami"),
    max_queries: int = typer.Option(5, min=1, max=100),
    per_query: int = typer.Option(10, min=1, max=100),
) -> None:
    """Find candidate chats and relevant message evidence."""
    database = _database()
    database.create_all()
    try:
        summary = asyncio.run(
            run_discovery(
                settings=get_settings(),
                database=database,
                profile_slug=profile,
                max_queries=max_queries,
                per_query=per_query,
                classifier=_classifier(),
            )
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(
        {
            "queries_run": summary.queries_run,
            "sources_created": summary.sources_created,
            "messages_examined": summary.messages_examined,
            "hits_created": summary.hits_created,
        }
    )


@app.command("sync-dialogs")
def sync_dialogs(
    profile: str = typer.Option("jetski-miami"),
    limit: int | None = typer.Option(None, min=1, max=10000),
) -> None:
    """Import groups already accessible to the authorized Telegram account."""
    database = _database()
    database.create_all()
    try:
        summary = asyncio.run(sync_account_dialogs(get_settings(), database, profile, limit))
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(
        {
            "dialogs_examined": summary.dialogs_examined,
            "groups_found": summary.groups_found,
            "sources_created": summary.sources_created,
        }
    )


@app.command("list-sources")
def list_sources(
    profile: str = typer.Option("jetski-miami"),
    limit: int = typer.Option(100, min=1, max=1000),
) -> None:
    """Show candidate and approved monitoring sources."""
    database = _database()
    database.create_all()
    with database.session() as session:
        rows = list(
            session.execute(
                select(SourceSubscription, ChatSource)
                .join(ChatSource, ChatSource.id == SourceSubscription.source_id)
                .join(SearchProfileRecord, SearchProfileRecord.id == SourceSubscription.profile_id)
                .where(SearchProfileRecord.slug == profile)
                .order_by(SourceSubscription.relevance_score.desc())
                .limit(limit)
            )
        )
    table = Table("Subscription", "Chat", "Username", "Score", "Evidence", "Status", "Access")
    for subscription, source in rows:
        table.add_row(
            str(subscription.id),
            source.title,
            f"@{source.username}" if source.username else "—",
            f"{subscription.relevance_score:.2f}",
            str(subscription.evidence_count),
            subscription.status,
            source.permission_status,
        )
    console.print(table)


@app.command("add-source")
def add_source(
    username: str = typer.Argument(..., help="@username or https://t.me/username"),
    profile: str = typer.Option("jetski-miami"),
) -> None:
    """Add one known public Telegram group as a candidate source."""
    database = _database()
    database.create_all()
    try:
        result = asyncio.run(
            add_public_source(get_settings(), database, profile, username)
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(
        {
            "subscription_id": result.subscription_id,
            "source_id": result.source_id,
            "title": result.title,
            "username": result.username,
        }
    )


@app.command("set-source-status")
def source_status(
    subscription_id: int = typer.Argument(..., min=1),
    status: str = typer.Argument(..., help="candidate, approved, paused or rejected"),
) -> None:
    """Approve, pause or reject one profile-specific Telegram source."""
    database = _database()
    database.create_all()
    with database.session() as session:
        subscription = session.get(SourceSubscription, subscription_id)
        if subscription is None:
            raise typer.BadParameter("Unknown subscription ID")
        try:
            set_subscription_status(session, subscription, status)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Source subscription {subscription_id} is now {status}.[/green]")


@app.command("monitor")
def monitor(
    profile: str = typer.Option("jetski-miami"),
    source_limit: int | None = typer.Option(None, min=1, max=1000),
    messages_per_source: int | None = typer.Option(None, min=1, max=5000),
) -> None:
    """Scan new messages from explicitly approved sources once."""
    database = _database()
    database.create_all()
    try:
        summary = asyncio.run(
            run_monitor(
                get_settings(),
                database,
                profile,
                _classifier(),
                source_limit,
                messages_per_source,
            )
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(summary)


@app.command("backfill")
def backfill(
    profile: str = typer.Option("jetski-miami"),
    lookback_days: int = typer.Option(180, min=1, max=3650),
    messages_per_term: int = typer.Option(50, min=1, max=500),
    max_service_terms: int = typer.Option(12, min=1, max=100),
    source_limit: int | None = typer.Option(None, min=1, max=1000),
) -> None:
    """Search approved Telegram group history for service-demand messages."""
    database = _database()
    database.create_all()
    try:
        summary = asyncio.run(
            run_backfill(
                get_settings(),
                database,
                profile,
                _classifier(),
                lookback_days,
                messages_per_term,
                max_service_terms,
                source_limit,
            )
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(summary)


@app.command("worker")
def worker(
    profile: str = typer.Option("jetski-miami"),
    interval: int | None = typer.Option(None, min=30, max=86400),
    discover_every: int = typer.Option(12, min=0, max=10000),
) -> None:
    """Continuously monitor approved sources and periodically expand discovery."""
    settings = get_settings()
    delay = interval or settings.monitor_interval_seconds
    cycle = 0
    classifier = _classifier()
    database = _database()
    database.create_all()
    notification_api = bot_api_from_settings(settings)
    console.print(
        f"[green]Worker started; scan interval is {delay}s. Press Ctrl+C to stop.[/green]"
    )
    try:
        while True:
            cycle_started_at = time.monotonic()
            cycle += 1
            if notification_api is not None:
                try:
                    bot_summary = poll_bot_updates(
                        settings, database, notification_api
                    )
                    if bot_summary.updates:
                        console.print({"cycle": cycle, "bot": bot_summary})
                except RuntimeError as exc:
                    console.print(f"[red]Notification bot: {exc}[/red]")
            try:
                summary = asyncio.run(
                    run_monitor(settings, database, profile, classifier)
                )
                console.print({"cycle": cycle, "monitor": summary})
                if discover_every and cycle % discover_every == 0:
                    discovery_summary = asyncio.run(
                        run_discovery(
                            settings,
                            database,
                            profile,
                            min(10, settings.discovery_max_queries),
                            settings.discovery_results_per_query,
                            classifier,
                        )
                    )
                    console.print({"cycle": cycle, "discovery": discovery_summary})
            except RuntimeError as exc:
                console.print(f"[red]{exc}[/red]")
            if notification_api is not None:
                try:
                    delivery = deliver_pending_notifications(
                        settings, database, notification_api
                    )
                    if delivery.attempted:
                        console.print({"cycle": cycle, "notifications": delivery})
                except RuntimeError as exc:
                    console.print(f"[red]Notification delivery: {exc}[/red]")
            elapsed = time.monotonic() - cycle_started_at
            time.sleep(max(0.0, delay - elapsed))
    except KeyboardInterrupt:
        console.print("[yellow]Worker stopped.[/yellow]")


@app.command("reclassify-signals")
def reclassify_signals(
    profile: str = typer.Option("jetski-miami"),
    limit: int | None = typer.Option(None, min=1, max=10000),
) -> None:
    """Re-check pending signals with the required Gemini pipeline."""
    database = _database()
    database.create_all()
    try:
        summary = reclassify_pending_signals(
            get_settings(), database, profile, _classifier(), limit
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(summary)


@app.command("stats")
def stats() -> None:
    """Show current database totals."""
    database = _database()
    database.create_all()
    with database.session() as session:
        values = {
            "profiles": session.scalar(select(func.count()).select_from(SearchProfileRecord)),
            "sources": session.scalar(select(func.count()).select_from(ChatSource)),
            "approved_sources": session.scalar(
                select(func.count())
                .select_from(SourceSubscription)
                .where(SourceSubscription.status == "approved")
            ),
            "active_signals": session.scalar(
                select(func.count())
                .select_from(Signal)
                .where(Signal.status != "rejected")
            ),
            "rejected_signals": session.scalar(
                select(func.count())
                .select_from(Signal)
                .where(Signal.status == "rejected")
            ),
            "leads": session.scalar(select(func.count()).select_from(Lead)),
        }
    console.print(values)


@app.command("serve")
def serve(
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None, min=1, max=65535),
    reload: bool = typer.Option(False),
) -> None:
    """Start the local API and operator dashboard."""
    import uvicorn

    settings = get_settings()
    bind_host = host or settings.server_host
    if bind_host not in {"127.0.0.1", "localhost", "::1"} and settings.admin_api_key is None:
        raise typer.BadParameter("Set ADMIN_API_KEY before binding the dashboard publicly")
    uvicorn.run(
        "leadfinder.api:app",
        host=bind_host,
        port=port or settings.server_port,
        reload=reload,
    )


@app.command("export-sources")
def export_sources(
    path: Annotated[Path, typer.Argument()] = DEFAULT_SOURCES_EXPORT,
) -> None:
    """Export the ranked source catalog to CSV."""
    database = _database()
    database.create_all()
    count = write_sources_csv(database, path)
    console.print(f"[green]Exported {count} sources to {path.resolve()}.[/green]")


@app.command("export-leads")
def export_leads(
    path: Annotated[Path, typer.Argument()] = DEFAULT_LEADS_EXPORT,
) -> None:
    """Export leads to CSV with source-message text, timestamp, and permalink."""
    database = _database()
    database.create_all()
    count = write_leads_csv(database, path)
    console.print(f"[green]Exported {count} leads to {path.resolve()}.[/green]")


@app.command("export-signals")
def export_signals(
    path: Annotated[Path, typer.Argument()] = DEFAULT_SIGNALS_EXPORT,
) -> None:
    """Export stored relevant-message signals to CSV."""
    database = _database()
    database.create_all()
    count = write_signals_csv(database, path)
    console.print(f"[green]Exported {count} signals to {path.resolve()}.[/green]")


if __name__ == "__main__":
    app()
