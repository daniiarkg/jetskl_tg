from __future__ import annotations

from vercel import workflow

WORKFLOW_NAME = "passive-monitor"
wf = workflow.Workflows()


def _state_payload(state) -> dict[str, object]:
    return {
        "desired_running": state.desired_running,
        "status": state.status,
        "profile": state.profile_slug,
        "interval_seconds": state.interval_seconds,
        "run_id": state.run_id,
        "last_cycle_started_at": (
            state.last_cycle_started_at.isoformat()
            if state.last_cycle_started_at is not None
            else None
        ),
        "last_cycle_finished_at": (
            state.last_cycle_finished_at.isoformat()
            if state.last_cycle_finished_at is not None
            else None
        ),
        "last_error": state.last_error,
    }


def _remaining_sleep(interval_seconds: int, elapsed_seconds: float) -> str:
    """Keep cycle starts one interval apart without overlapping slow scans."""
    seconds = max(1.0, float(interval_seconds) - max(0.0, elapsed_seconds))
    return f"{seconds:.3f} seconds"


async def _execute_monitor_cycle(
    profile_slug: str,
    generation: str,
) -> dict[str, object]:
    import time
    from dataclasses import asdict

    from leadfinder.classification import build_classifier
    from leadfinder.config import get_settings
    from leadfinder.db import Database
    from leadfinder.models import WorkflowState, utc_now
    from leadfinder.monitor import run_monitor
    from leadfinder.notifications import (
        bot_api_from_settings,
        deliver_pending_notifications,
        poll_bot_updates,
    )

    started_monotonic = time.monotonic()
    settings = get_settings()
    database = Database(settings)
    database.create_all()

    with database.session() as session:
        state = session.get(WorkflowState, WORKFLOW_NAME)
        if (
            state is None
            or not state.desired_running
            or state.generation != generation
        ):
            return {"continue": False, "elapsed_seconds": 0.0}
        state.status = "running"
        state.last_cycle_started_at = utc_now()
        state.last_error = None
        state.updated_at = utc_now()

    try:
        bot_result: dict[str, object] | None = None
        bot_api = bot_api_from_settings(settings)
        if bot_api is not None:
            bot_result = asdict(poll_bot_updates(settings, database, bot_api))

        monitor_result = asdict(
            await run_monitor(
                settings,
                database,
                profile_slug,
                build_classifier(settings),
            )
        )

        notification_result: dict[str, object] | None = None
        if bot_api is not None:
            notification_result = asdict(
                deliver_pending_notifications(settings, database, bot_api)
            )

        elapsed_seconds = time.monotonic() - started_monotonic
        with database.session() as session:
            state = session.get(WorkflowState, WORKFLOW_NAME)
            should_continue = bool(
                state is not None
                and state.desired_running
                and state.generation == generation
            )
            if state is not None and state.generation == generation:
                state.status = "sleeping" if should_continue else "stopped"
                state.last_cycle_finished_at = utc_now()
                state.last_error = None
                state.updated_at = utc_now()
        return {
            "continue": should_continue,
            "elapsed_seconds": elapsed_seconds,
            "monitor": monitor_result,
            "bot": bot_result,
            "notifications": notification_result,
        }
    except Exception as exc:
        with database.session() as session:
            state = session.get(WorkflowState, WORKFLOW_NAME)
            if state is not None and state.generation == generation:
                state.status = "error"
                state.last_cycle_finished_at = utc_now()
                state.last_error = str(exc)[:4000]
                state.updated_at = utc_now()
        raise


@wf.step(max_retries=3)
async def execute_monitor_cycle(
    profile_slug: str,
    generation: str,
) -> dict[str, object]:
    return await _execute_monitor_cycle(profile_slug, generation)


async def passive_monitor_workflow(
    profile_slug: str,
    interval_seconds: int,
    generation: str,
) -> dict[str, object]:
    cycles = 0
    while True:
        try:
            result = await execute_monitor_cycle(profile_slug, generation)
        except Exception:
            # The failed step already stored a visible error. A later cycle may recover
            # from a transient Telegram, Gemini, or database outage.
            await workflow.sleep(f"{interval_seconds} seconds")
            continue
        if not result.get("continue"):
            return {"status": "stopped", "cycles": cycles}
        cycles += 1
        await workflow.sleep(
            _remaining_sleep(
                interval_seconds,
                float(result.get("elapsed_seconds") or 0.0),
            )
        )


# Vercel discovers this file through the source-layout path ``src.leadfinder`` while
# the installed application imports it as ``leadfinder``. Pin the public workflow ID
# to the deployment entrypoint so both imports start the same registered workflow.
passive_monitor_workflow.__module__ = "src.leadfinder.workflows"
passive_monitor_workflow = wf.workflow(passive_monitor_workflow)


async def start_passive_monitor(
    settings,
    database,
    profile_slug: str = "jetski-miami",
    interval_seconds: int = 60,
) -> dict[str, object]:
    import os
    from uuid import uuid4

    from sqlalchemy import select

    from leadfinder.models import SearchProfileRecord, WorkflowState, utc_now

    if interval_seconds != 60:
        raise RuntimeError("Production passive monitoring is fixed at 60 seconds")
    settings.require_telegram_credentials()
    settings.require_gemini_api_key()
    if os.getenv("VERCEL") and settings.telegram_session_string is None:
        raise RuntimeError("TELEGRAM_SESSION_STRING must be configured on Vercel")
    database.create_all()
    with database.session() as session:
        profile_exists = session.scalar(
            select(SearchProfileRecord.id).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile_exists is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}")
        current = session.get(WorkflowState, WORKFLOW_NAME)
        if (
            current is not None
            and current.desired_running
            and current.run_id
            and current.status in {"queued", "running", "sleeping"}
        ):
            return _state_payload(current)

        generation = uuid4().hex
        if current is None:
            current = WorkflowState(name=WORKFLOW_NAME)
            session.add(current)
        current.generation = generation
        current.profile_slug = profile_slug
        current.interval_seconds = interval_seconds
        current.desired_running = True
        current.status = "starting"
        current.run_id = None
        current.last_cycle_started_at = None
        current.last_cycle_finished_at = None
        current.last_error = None
        current.updated_at = utc_now()

    try:
        run = await workflow.start(
            passive_monitor_workflow,
            profile_slug,
            interval_seconds,
            generation,
        )
    except Exception as exc:
        with database.session() as session:
            state = session.get(WorkflowState, WORKFLOW_NAME)
            if state is not None and state.generation == generation:
                state.desired_running = False
                state.status = "error"
                state.last_error = str(exc)[:4000]
                state.updated_at = utc_now()
        raise RuntimeError(f"Could not start Vercel Workflow: {exc}") from exc

    with database.session() as session:
        state = session.get(WorkflowState, WORKFLOW_NAME)
        if state is None or state.generation != generation:
            raise RuntimeError("Workflow state changed while the run was starting")
        state.run_id = run.run_id
        state.status = "queued"
        state.updated_at = utc_now()
        return _state_payload(state)


def stop_passive_monitor(database) -> dict[str, object]:
    from leadfinder.models import WorkflowState, utc_now

    database.create_all()
    with database.session() as session:
        state = session.get(WorkflowState, WORKFLOW_NAME)
        if state is None:
            return {
                "desired_running": False,
                "status": "stopped",
                "profile": "jetski-miami",
                "interval_seconds": 60,
                "run_id": None,
                "last_cycle_started_at": None,
                "last_cycle_finished_at": None,
                "last_error": None,
            }
        state.desired_running = False
        state.status = "stopping"
        state.updated_at = utc_now()
        return _state_payload(state)
