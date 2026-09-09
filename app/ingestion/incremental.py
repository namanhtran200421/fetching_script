from datetime import datetime, timedelta, timezone


def _parse_timestamp(value):
    if not value or not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_incremental_plan(
    started_at,
    previous_manifest,
    enabled=True,
    reconciliation_interval_days=7,
    lookback_seconds=300,
):
    """Choose a safe full or incremental run from the last complete baseline."""
    previous_manifest = previous_manifest or {}
    previous_run_id = previous_manifest.get("run_id")
    last_full_value = previous_manifest.get("last_full_reconciliation_at")
    last_full = _parse_timestamp(last_full_value)

    reason = None
    if not enabled:
        reason = "incremental ingestion disabled"
    elif not previous_run_id:
        reason = "no complete baseline"
    elif last_full is None:
        reason = "baseline predates incremental metadata"
    elif started_at - last_full >= timedelta(days=reconciliation_interval_days):
        reason = "scheduled full reconciliation is due"

    if reason:
        return {
            "mode": "full",
            "reason": reason,
            "previous_run_id": previous_run_id,
            "modified_after": None,
            "full_reconciliation": True,
            "last_full_reconciliation_at": started_at.isoformat(),
        }

    previous_boundary = _parse_timestamp(
        previous_manifest.get("completed_at")
        or previous_manifest.get("started_at")
    )
    if previous_boundary is None:
        return {
            "mode": "full",
            "reason": "baseline has no valid completion timestamp",
            "previous_run_id": previous_run_id,
            "modified_after": None,
            "full_reconciliation": True,
            "last_full_reconciliation_at": started_at.isoformat(),
        }

    modified_after = previous_boundary - timedelta(seconds=lookback_seconds)
    return {
        "mode": "incremental",
        "reason": "complete baseline is recent",
        "previous_run_id": previous_run_id,
        "modified_after": modified_after.isoformat(),
        "full_reconciliation": False,
        "last_full_reconciliation_at": last_full_value,
    }
