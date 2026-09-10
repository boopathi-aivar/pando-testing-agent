from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from database import tbl_projects
from tools.dynamodb_tools import get_results_for_project, _from_dynamo
from routers.auth import get_current_user
from services.field_compare import apply_comparison, field_is_required

router = APIRouter(tags=["dashboard"])


def _parse_dt(ts) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day_key(dt: datetime) -> str:
    return dt.date().isoformat()


@router.get("/dashboard/summary")
def dashboard_summary(
    project_id: Optional[str] = Query(None, description="Limit insights to one project"),
    _user=Depends(get_current_user),
):
    """Aggregate test results for the insights dashboard. Optional project filter."""
    all_projects = [_from_dynamo(i) for i in tbl_projects().scan().get("Items", [])]
    project_options = [
        {
            "project_id": p.get("project_id"),
            "project_name": p.get("project_name") or p.get("project_id"),
        }
        for p in all_projects
        if p.get("project_id")
    ]
    project_options.sort(key=lambda p: (p["project_name"] or "").lower())
    project_names = {p["project_id"]: p["project_name"] for p in project_options}
    mandatory_by_project = {
        p.get("project_id"): p.get("mandatory_fields") or []
        for p in all_projects
        if p.get("project_id")
    }

    scoped = all_projects
    if project_id:
        scoped = [p for p in all_projects if p.get("project_id") == project_id]

    results = []
    for proj in scoped:
        pid = proj.get("project_id")
        if not pid:
            continue
        mandatory = mandatory_by_project.get(pid) or []
        for item in get_results_for_project(pid, limit=200):
            item["_project_name"] = project_names.get(pid, pid)
            apply_comparison(
                item,
                payload=item.get("raw_payload") or {},
                mandatory_fields=mandatory,
            )
            results.append(item)

    now = datetime.now(tz=timezone.utc)
    today = now.date()
    yesterday = today - timedelta(days=1)

    scores = []
    status_counts = {"passed": 0, "warning": 0, "failed": 0, "unscored": 0}
    tests_today = 0
    tests_yesterday = 0
    field_issues = {"wrong": 0, "missing": 0, "unverified": 0}
    by_day: dict[str, list[float]] = defaultdict(list)
    by_carrier: dict[str, list[float]] = defaultdict(list)
    by_project: dict[str, list[float]] = defaultdict(list)

    dated = []
    for r in results:
        pid = r.get("project_id")
        mandatory_set = {f.lower() for f in (mandatory_by_project.get(pid) or [])}
        dt = _parse_dt(r.get("timestamp") or r.get("created_at"))
        score = r.get("overall_score")
        try:
            score_f = float(score) if score is not None and score != "" else None
        except (TypeError, ValueError):
            score_f = None

        status = (r.get("status") or "failed").lower()
        if status not in status_counts:
            status = "failed"
        status_counts[status] += 1

        scored = score_f is not None and status != "unscored"
        if scored:
            scores.append(score_f)

        if dt:
            dated.append((dt, r, score_f if scored else None))
            d = dt.date()
            if d == today:
                tests_today += 1
            elif d == yesterday:
                tests_yesterday += 1
            if scored:
                by_day[_day_key(dt)].append(score_f)

        carrier = (r.get("vendor_name") or "").strip() or "Unknown"
        if scored:
            by_carrier[carrier].append(score_f)
            by_project[r.get("project_id") or "unknown"].append(score_f)

        validations = [v for v in (r.get("field_validations") or []) if isinstance(v, dict)]
        has_required = bool(mandatory_set) or any(v.get("is_mandatory") for v in validations)
        for v in validations:
            if has_required and not field_is_required(v.get("field_name"), v, mandatory_set):
                continue
            st = (v.get("status") or "").lower()
            if st in field_issues:
                field_issues[st] += 1

    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    failed_pct = round(status_counts["failed"] / len(results) * 100, 1) if results else 0

    tests_by_day = defaultdict(int)
    for dt, _r, _s in dated:
        tests_by_day[_day_key(dt)] += 1
    day_series = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        key = d.isoformat()
        vals = by_day.get(key, [])
        day_series.append({
            "date": d.strftime("%b %d"),
            "iso": key,
            "avg_score": round(sum(vals) / len(vals), 1) if vals else None,
            "tests": tests_by_day.get(key, 0),
        })

    carriers = []
    for name, vals in by_carrier.items():
        carriers.append({
            "carrier": name,
            "avg_score": round(sum(vals) / len(vals), 1),
            "tests": len(vals),
        })
    carriers.sort(key=lambda x: x["tests"], reverse=True)

    project_scores = []
    for pid, vals in by_project.items():
        project_scores.append({
            "project_id": pid,
            "project_name": project_names.get(pid, pid),
            "avg_score": round(sum(vals) / len(vals), 1),
            "tests": len(vals),
        })
    project_scores.sort(key=lambda x: x["avg_score"], reverse=True)

    dated.sort(key=lambda x: x[0], reverse=True)
    recent = []
    for dt, r, score_f in dated[:12]:
        recent.append({
            "result_id":      r.get("result_id"),
            "project_id":     r.get("project_id"),
            "project_name":   r.get("_project_name"),
            "invoice_number": r.get("invoice_number") or "unknown",
            "vendor_name":    r.get("vendor_name"),
            "overall_score":  score_f,
            "status":         r.get("status") or "failed",
            "timestamp":      r.get("timestamp") or r.get("created_at"),
        })

    return {
        "generated_at": now.isoformat(),
        "project_count": len(all_projects),
        "selected_project_id": project_id,
        "project_options": project_options,
        "stats": {
            "total_tests": len(results),
            "tests_today": tests_today,
            "tests_yesterday": tests_yesterday,
            "avg_score": avg_score,
            "failed": status_counts["failed"],
            "failed_pct": failed_pct,
            "passed": status_counts["passed"],
            "warning": status_counts["warning"],
            "unscored": status_counts["unscored"],
        },
        "status_counts": status_counts,
        "score_by_day": day_series,
        "carriers": carriers[:10],
        "projects": project_scores,
        "field_issues": field_issues,
        "recent": recent,
    }
