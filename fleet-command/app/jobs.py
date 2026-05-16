from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RUNS_DIR = Path("/data/runs")

STATUS_PENDING   = "pending"
STATUS_RUNNING   = "running"
STATUS_REVIEWING = "reviewing"
STATUS_DONE      = "done"
STATUS_FAILED    = "failed"
STATUS_SPLIT     = "split"


_PIPELINE_EXECUTION_ORDER = [
    "project_manager", "manager", "generator", "assembler", "reviewer", "supervisor"
]


def _default_pipeline() -> list[str]:
    """Return the full pipeline stages that have a harness assigned, in execution order."""
    try:
        from app.roles import load_roles
        roles = load_roles()
        assigned = {stage for stage, cfg in roles.items() if cfg.get("harness_id")}
        pipeline = [s for s in _PIPELINE_EXECUTION_ORDER if s in assigned]
        return pipeline if pipeline else ["generator"]
    except Exception:
        return ["generator"]


def _run_dir(job_id: str) -> Path:
    return _RUNS_DIR / job_id


def create_job(spec: dict[str, Any]) -> dict[str, Any]:
    job_id = str(uuid.uuid4())[:8]
    job: dict[str, Any] = {
        "id": job_id,
        "type": spec.get("type", "ha_dashboard"),
        "spec": spec.get("spec", ""),
        "target_dashboard": spec.get("target_dashboard", "fleet-output"),
        "pipeline": spec.get("pipeline") or _default_pipeline(),
        "status": STATUS_PENDING,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stages": {},
        "log": [],
        "final_output": None,
        "parent_job_id": spec.get("parent_job_id"),
        "child_job_ids": [],
        "message_log": [],
    }
    d = _run_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    return job


def load_job(job_id: str) -> dict[str, Any] | None:
    path = _run_dir(job_id) / "job.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_job(job: dict[str, Any]) -> None:
    d = _run_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    if not _RUNS_DIR.exists():
        return []
    jobs = []
    for d in sorted(_RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        jf = d / "job.json"
        if jf.exists():
            try:
                jobs.append(json.loads(jf.read_text(encoding="utf-8")))
            except Exception:
                pass
        if len(jobs) >= limit:
            break
    return jobs


def append_log(job: dict[str, Any], stage: str, message: str) -> None:
    job.setdefault("log", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "msg": message,
    })


def log_message(
    job: dict[str, Any],
    sender: str,
    recipient: str,
    msg_type: str,
    content: str,
    stage: str,
    block: str = "",
) -> None:
    """Log an inter-worker communication entry. msg_type: 'comm' | 'code'."""
    job.setdefault("message_log", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "sender": sender,
        "recipient": recipient,
        "type": msg_type,
        "content": content,
        "stage": stage,
        "block": block,
    })


def write_stage_output(job_id: str, stage: str, content: str) -> None:
    p = _run_dir(job_id) / f"stage_{stage}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def read_stage_output(job_id: str, stage: str) -> str | None:
    p = _run_dir(job_id) / f"stage_{stage}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else None


def write_stage_input(job_id: str, stage: str, content: str) -> None:
    p = _run_dir(job_id) / f"stage_{stage}_input.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def read_stage_input(job_id: str, stage: str) -> str | None:
    p = _run_dir(job_id) / f"stage_{stage}_input.txt"
    return p.read_text(encoding="utf-8") if p.exists() else None


def cancel_job(job_id: str) -> bool:
    job = load_job(job_id)
    if not job:
        return False
    (_run_dir(job_id) / "cancel").touch()
    job["status"] = "cancelled"
    append_log(job, "system", "Cancelled by user")
    save_job(job)
    return True


def is_cancelled(job_id: str) -> bool:
    return (_run_dir(job_id) / "cancel").exists()


def restart_job(job_id: str) -> dict[str, Any] | None:
    job = load_job(job_id)
    if not job:
        return None
    cancel_flag = _run_dir(job_id) / "cancel"
    if cancel_flag.exists():
        cancel_flag.unlink()
    job["status"] = STATUS_PENDING
    job["stages"] = {}
    job["log"] = []
    job["final_output"] = None
    append_log(job, "system", "Restarted by user")
    save_job(job)
    return job


def rerun_from_stage(job_id: str, stage: str) -> dict[str, Any] | None:
    job = load_job(job_id)
    if not job:
        return None
    pipeline = job.get("pipeline", [])
    if stage not in pipeline:
        return None
    idx = pipeline.index(stage)
    threads = job.get("threads", {})
    for s in pipeline[idx:]:
        job["stages"].pop(s, None)
        threads.pop(s, None)
        p = _run_dir(job_id) / f"stage_{s}.txt"
        if p.exists():
            p.unlink()
    job["threads"] = threads
    final = _run_dir(job_id) / "stage_final.txt"
    if final.exists():
        final.unlink()
    job["final_output"] = None
    cancel_flag = _run_dir(job_id) / "cancel"
    if cancel_flag.exists():
        cancel_flag.unlink()
    job["status"] = STATUS_PENDING
    append_log(job, "system", f"Re-run from: {stage}")
    save_job(job)
    return job


def delete_job(job_id: str) -> bool:
    import shutil
    d = _run_dir(job_id)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True
