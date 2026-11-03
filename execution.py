"""Controlled task execution engine (ReFlow Phase 4, v1).

Design notes (read before extending):
- Single-user local demonstration. Approvals, execution records and history
  live in the browser (localStorage ``reflow:execution:v1``); this backend is
  stateless and keeps only process-local guards (a single-flight lock and a
  bounded idempotency map). There is no authentication, so approval records
  are workflow-level, not cryptographic: this module verifies internal
  consistency (approval snapshot matches the executed task, dependencies are
  reported complete) but cannot prove who clicked Approve.
- All v1 action handlers are local and deterministic: no model calls, no
  network, no email sending. Handlers only confirm success after the effect
  happened (file written, draft built).
- Never log task content, file content or user data: logs carry counts,
  action types and byte sizes only.
"""

import hashlib
import json
import logging
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("reflow")

OUTPUT_DIR_NAME = "reflow-outputs"
ALLOWED_EXTENSIONS = {".md", ".txt", ".json"}
MAX_FILENAME_LEN = 80
MAX_FILE_BYTES = 256 * 1024
MAX_RESULT_PREVIEW_CHARS = 500
EXECUTION_TIMEOUT_S = 30
MAX_IDEMPOTENCY_RECORDS = 500
FINGERPRINT_VERSION = "exec-v1"


def _default_output_dir() -> Path:
    """Authorized file sandbox: ``reflow-outputs/`` next to this module."""
    return Path(__file__).resolve().parent / OUTPUT_DIR_NAME


# --------------------------------------------------------------------------
# Fingerprints: bind an approval to the exact approved task + action.
# --------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_of(payload: dict) -> str:
    raw = FINGERPRINT_VERSION + "|" + canonical_json(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Errors: controlled, user-facing, free of sensitive content.
# --------------------------------------------------------------------------


class ExecutionError(Exception):
    """Maps to an HTTP error with a safe, user-facing message."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ActionFailed(Exception):
    """A handler failed after validation passed. Message must stay safe."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------
# Request / response schemas (additive; existing contracts untouched).
# --------------------------------------------------------------------------


class TaskSnapshot(BaseModel):
    """Task exactly as approved: any difference invalidates the approval."""

    id: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=1000)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    needs_info: list[str] = Field(default_factory=list, max_length=10)
    status: str = Field(default="ready", max_length=16)


class ActionRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)


class ApprovalRecord(BaseModel):
    status: str = Field(min_length=1, max_length=16)
    approved_at: datetime
    snapshot: dict


class DepState(BaseModel):
    """Client-reported dependency state (single-user demo: documented trust)."""

    id: str = Field(min_length=1, max_length=32)
    planning_status: str = Field(default="", max_length=16)
    execution_status: str = Field(default="none", max_length=16)
    local: Optional[str] = Field(default=None, max_length=16)


class ExecuteRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)
    task: TaskSnapshot
    action: ActionRequest
    approval: ApprovalRecord
    context_tasks: list[DepState] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ExecuteResponse(BaseModel):
    execution_id: str
    status: str = "completed"
    action_type: str
    result_summary: str
    result: dict = Field(default_factory=dict)
    fingerprint: str
    started_at: str
    finished_at: str
    duplicate: bool = False


# --------------------------------------------------------------------------
# Per-action parameter schemas.
# --------------------------------------------------------------------------


class DocumentCreateParams(BaseModel):
    filename: str = Field(min_length=1, max_length=MAX_FILENAME_LEN)
    title: str = Field(default="", max_length=200)
    format: Literal["markdown", "text"] = "markdown"
    confirm_overwrite: bool = False


class PlanOrganizeParams(BaseModel):
    filename: str = Field(min_length=1, max_length=MAX_FILENAME_LEN)
    confirm_overwrite: bool = False


class ResearchSummarizeParams(BaseModel):
    topic: str = Field(default="", max_length=200)
    max_points: int = Field(default=5, ge=1, le=10)


class EmailDraftParams(BaseModel):
    to: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=200)
    tone: Literal["neutral", "formal", "friendly"] = "neutral"


class FileOp(BaseModel):
    op: Literal["mkdir", "move"]
    name: str = Field(default="", max_length=MAX_FILENAME_LEN)
    src: str = Field(default="", max_length=MAX_FILENAME_LEN)
    dest: str = Field(default="", max_length=MAX_FILENAME_LEN)
    confirm_overwrite: bool = False


class FileOrganizeParams(BaseModel):
    operations: list[FileOp] = Field(min_length=1, max_length=10)


# --------------------------------------------------------------------------
# File sandbox: flat namespace, allowlisted extensions, atomic writes.
# --------------------------------------------------------------------------


def _safe_name(value: str, what: str) -> str:
    name = (value or "").strip()
    if not name or name in (".", ".."):
        raise ActionFailed(f"Invalid {what}: name is required.", 422)
    if len(name) > MAX_FILENAME_LEN:
        raise ActionFailed(f"Invalid {what}: name is too long.", 422)
    if "/" in name or "\\" in name or "\x00" in name:
        raise ActionFailed(f"Invalid {what}: subdirectories are not allowed.", 422)
    if os.path.isabs(name):
        raise ActionFailed(f"Invalid {what}: absolute paths are not allowed.", 422)
    return name


def _checked_path(base: Path, name: str, what: str, extensions: set) -> Path:
    safe = _safe_name(name, what)
    suffix = Path(safe).suffix.lower()
    if suffix not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise ActionFailed(f"Invalid {what}: extension must be one of {allowed}.", 422)
    target = (base / safe).resolve()
    if target != base.resolve() and base.resolve() not in target.parents:
        raise ActionFailed(f"Invalid {what}: outside the authorized directory.", 422)
    return target


def _write_file_atomic(target: Path, content: str, confirm_overwrite: bool, what: str) -> int:
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ActionFailed(f"{what} is too large: {MAX_FILE_BYTES} bytes maximum.", 422)
    if target.exists() and not confirm_overwrite:
        raise ActionFailed(
            f"{target.name} already exists: confirm overwrite to replace it.", 409
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return len(data)


# --------------------------------------------------------------------------
# Action handlers: (task, params, context, output_dir) -> (summary, result).
# Local and deterministic in v1: no model calls, no network, no sending.
# --------------------------------------------------------------------------


def _handle_document_create(task, params, context, output_dir):
    title = params.title.strip() or task.title
    detail = task.detail.strip() or "(No detail provided.)"
    criteria = "\n".join(f"- {item}" for item in task.success_criteria)
    if params.format == "text":
        content = (
            f"{title}\n\n{detail}\n\nSuccess criteria:\n"
            + "\n".join(f"  - {item}" for item in task.success_criteria)
            + f"\n\nPrepared by ReFlow from approved task {task.id}. "
            "Draft only - review before use.\n"
        )
    else:
        content = (
            f"# {title}\n\n{detail}\n\n## Success criteria\n{criteria}\n\n"
            f"---\nPrepared by ReFlow from approved task {task.id}. "
            "Draft only - review before use.\n"
        )
    target = _checked_path(output_dir, params.filename, "filename", {".md", ".txt"})
    size = _write_file_atomic(target, content, params.confirm_overwrite, "Document")
    summary = f"Document saved as {target.name} ({size} bytes)."
    return summary, {
        "filename": target.name,
        "bytes": size,
        "title": title,
        "preview": content[:MAX_RESULT_PREVIEW_CHARS],
    }


def _handle_plan_organize(task, params, context, output_dir):
    lines = [
        f"# Execution plan: {task.title}",
        "",
        task.detail.strip() or "(No detail provided.)",
        "",
        "## Prerequisites",
    ]
    states = {item.id: item for item in context}
    if task.depends_on:
        for dep_id in task.depends_on:
            dep = states.get(dep_id)
            state = dep.execution_status if dep else "unknown"
            lines.append(f"- {dep_id}: {state}")
    else:
        lines.append("- None: this task can start right away.")
    lines += ["", "## Checklist"] + [f"- [ ] {item}" for item in task.success_criteria]
    lines.append(f"\nPrepared by ReFlow from approved task {task.id}.")
    content = "\n".join(lines) + "\n"
    target = _checked_path(output_dir, params.filename, "filename", {".md", ".txt"})
    size = _write_file_atomic(target, content, params.confirm_overwrite, "Planning document")
    summary = f"Planning document saved as {target.name} ({size} bytes)."
    return summary, {
        "filename": target.name,
        "bytes": size,
        "preview": content[:MAX_RESULT_PREVIEW_CHARS],
    }


def _handle_research_summarize(task, params, context, output_dir):
    _ = (output_dir, context)
    topic = params.topic.strip() or task.title
    facts = []
    if task.detail.strip():
        facts.append(task.detail.strip())
    facts.extend(task.success_criteria[: params.max_points])
    facts = facts[: params.max_points] or ["(No facts provided in the task.)"]
    brief = {
        "topic": topic,
        "objective": task.title,
        "provided_facts": facts,
        "open_questions": list(task.needs_info) or ["(No open questions recorded.)"],
        "sources": [],
        "verification": (
            "UNVERIFIED DRAFT: every statement above comes from the approved "
            "task itself. No external source was consulted and nothing here "
            "is independently verified."
        ),
    }
    summary = (
        f"Research brief for '{topic}': {len(facts)} provided fact(s), "
        f"{len(task.needs_info)} open question(s). No external sources consulted."
    )
    return summary, {"brief": brief}


def _handle_email_draft(task, params, context, output_dir):
    _ = (output_dir, context)
    subject = params.subject.strip() or f"Re: {task.title}"
    greeting = {"formal": "Dear recipient,", "friendly": "Hi there,", "neutral": "Hello,"}[
        params.tone
    ]
    body_lines = [
        greeting,
        "",
        task.detail.strip() or f"Following up on: {task.title}.",
        "",
        "Planned next steps:",
        *[f"- {item}" for item in task.success_criteria],
        "",
        "Best regards,",
        "[Your name]",
    ]
    body = "\n".join(body_lines)
    summary = f"Email draft prepared (subject: '{subject}'). Not sent."
    return summary, {
        "to": params.to,
        "subject": subject,
        "body": body,
        "note": "Draft only - ReFlow never sends emails.",
    }


def _handle_file_organize(task, params, context, output_dir):
    _ = (task, context)
    done = []
    for op in params.operations:
        if op.op == "mkdir":
            name = _safe_name(op.name, "directory name")
            target = (output_dir / name).resolve()
            if output_dir.resolve() not in target.parents:
                raise ActionFailed("Invalid directory: outside the authorized directory.", 422)
            if target.exists():
                raise ActionFailed(f"{name} already exists.", 409)
            target.mkdir(parents=True, exist_ok=False)
            done.append({"op": "mkdir", "path": name, "ok": True})
        else:  # move, inside the sandbox only
            src = _checked_path(output_dir, op.src, "source", ALLOWED_EXTENSIONS)
            dest = _checked_path(output_dir, op.dest, "destination", ALLOWED_EXTENSIONS)
            if not src.exists() or not src.is_file():
                raise ActionFailed(f"Source not found: {op.src}.", 422)
            if dest.exists() and not op.confirm_overwrite:
                raise ActionFailed(
                    f"{op.dest} already exists: confirm overwrite to replace it.", 409
                )
            os.replace(src, dest)
            done.append({"op": "move", "path": op.dest, "ok": True})
    summary = f"Organized {len(done)} file operation(s) in {OUTPUT_DIR_NAME}/."
    return summary, {"operations": done}


ACTIONS = {
    "document.create": {
        "params": DocumentCreateParams,
        "handler": _handle_document_create,
        "description": "Generate a report or draft and save it in the ReFlow output directory.",
        "side_effects": True,
        "safe_retry": False,
    },
    "plan.organize": {
        "params": PlanOrganizeParams,
        "handler": _handle_plan_organize,
        "description": "Generate a schedule or checklist document with dependency states.",
        "side_effects": True,
        "safe_retry": False,
    },
    "research.summarize": {
        "params": ResearchSummarizeParams,
        "handler": _handle_research_summarize,
        "description": "Build an explicitly unverified brief from the task itself. No sources invented.",
        "side_effects": False,
        "safe_retry": True,
    },
    "email.draft": {
        "params": EmailDraftParams,
        "handler": _handle_email_draft,
        "description": "Generate an email draft. Never sent.",
        "side_effects": False,
        "safe_retry": True,
    },
    "file.organize": {
        "params": FileOrganizeParams,
        "handler": _handle_file_organize,
        "description": "Create directories or move files inside the ReFlow output directory. Never deletes.",
        "side_effects": True,
        "safe_retry": False,
    },
}


def action_specs() -> list:
    """Public registry listing for the frontend action picker."""
    return [
        {
            "type": action_type,
            "description": spec["description"],
            "side_effects": spec["side_effects"],
            "safe_retry": spec["safe_retry"],
        }
        for action_type, spec in ACTIONS.items()
    ]


# --------------------------------------------------------------------------
# Engine: single-flight + idempotency + ordered validation gates.
# --------------------------------------------------------------------------

_EXEC_LOCK = threading.Lock()
_IDEMPOTENCY: "OrderedDict[str, dict]" = OrderedDict()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_payload(task: TaskSnapshot, action_type: str, params: dict) -> dict:
    return {
        "task": {
            "id": task.id,
            "title": task.title,
            "detail": task.detail,
            "depends_on": list(task.depends_on),
            "success_criteria": list(task.success_criteria),
            "needs_info": list(task.needs_info),
            "status": task.status,
        },
        "action": {"type": action_type, "params": params},
    }


def _check_approval(body: ExecuteRequest) -> str:
    """Returns the request fingerprint after binding approval to content."""
    status = (body.approval.status or "").lower()
    if status == "rejected":
        raise ExecutionError(409, "Task was rejected: execution is not allowed.")
    if status == "revoked":
        raise ExecutionError(409, "Approval was revoked: execution is not allowed.")
    if status != "approved":
        raise ExecutionError(422, "Explicit approval is required before execution.")
    snapshot = body.approval.snapshot
    if not isinstance(snapshot, dict) or "task" not in snapshot or "action" not in snapshot:
        raise ExecutionError(422, "Approval record is malformed: approve the task again.")
    current = _snapshot_payload(body.task, body.action.type, body.action.params)
    if fingerprint_of(snapshot) != fingerprint_of(current):
        raise ExecutionError(
            409, "Task changed since approval: review and approve it again."
        )
    return fingerprint_of(current)


def _check_readiness(body: ExecuteRequest) -> None:
    task = body.task
    if task.status == "blocked":
        raise ExecutionError(409, "Task is blocked: resolve its blockers first.")
    if task.status in ("done", "skipped"):
        raise ExecutionError(409, "Task is already finished.")
    if task.status not in ("ready", "pending"):
        raise ExecutionError(422, f"Task status '{task.status}' cannot execute.")
    if task.needs_info:
        missing = ", ".join(task.needs_info)
        raise ExecutionError(422, f"Missing prerequisites ({missing}): cannot execute.")
    states = {item.id: item for item in body.context_tasks}
    for dep_id in task.depends_on:
        dep = states.get(dep_id)
        if dep is None:
            raise ExecutionError(422, f"Unknown dependency '{dep_id}': cannot verify readiness.")
        if dep.execution_status == "failed":
            raise ExecutionError(409, f"Dependency '{dep_id}' failed: cannot execute.")
        if dep.execution_status == "completed" or (dep.local or "") in ("done", "skipped"):
            continue
        raise ExecutionError(409, f"Dependency '{dep_id}' is not completed yet.")


def _parse_params(action_type: str, params: dict):
    model = ACTIONS[action_type]["params"]
    try:
        return model.model_validate(params or {})
    except ValidationError as error:
        first = error.errors()[0]["msg"] if error.errors() else "invalid value"
        raise ExecutionError(422, f"Invalid parameters for {action_type}: {first}.")


def _record_idempotent(key: str, fingerprint: str, response: dict) -> None:
    _IDEMPOTENCY[key] = {"fingerprint": fingerprint, "response": response}
    while len(_IDEMPOTENCY) > MAX_IDEMPOTENCY_RECORDS:
        _IDEMPOTENCY.popitem(last=False)


def reset_engine_state() -> None:
    """Clears process-local guards. Tests only; never called by routes."""
    _IDEMPOTENCY.clear()


def run_execution(body: ExecuteRequest, output_dir=None) -> ExecuteResponse:
    """Validates and runs one approved action. Raises ExecutionError only."""
    fingerprint = _check_approval(body)
    _check_readiness(body)
    if body.action.type not in ACTIONS:
        supported = ", ".join(sorted(ACTIONS))
        raise ExecutionError(
            422, f"Unsupported action '{body.action.type}'. Supported: {supported}."
        )
    params = _parse_params(body.action.type, body.action.params)

    stored = _IDEMPOTENCY.get(body.idempotency_key)
    if stored is not None:
        if stored["fingerprint"] != fingerprint:
            raise ExecutionError(409, "Idempotency key was already used for another task.")
        logger.info("Duplicate execution ignored (key replayed).")
        return ExecuteResponse.model_validate({**stored["response"], "duplicate": True})

    if not _EXEC_LOCK.acquire(blocking=False):
        raise ExecutionError(409, "Another task is already executing: try again later.")
    started_at = _utcnow()
    try:
        handler = ACTIONS[body.action.type]["handler"]
        base = Path(output_dir) if output_dir else _default_output_dir()
        base.mkdir(parents=True, exist_ok=True)
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(handler, body.task, params, body.context_tasks, base)
            try:
                summary, result = future.result(timeout=EXECUTION_TIMEOUT_S)
            except FuturesTimeoutError:
                raise ExecutionError(504, "Execution timed out: try again later.")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        finished_at = _utcnow()
        response = ExecuteResponse(
            execution_id=uuid.uuid4().hex,
            action_type=body.action.type,
            result_summary=summary,
            result=result,
            fingerprint=fingerprint,
            started_at=started_at,
            finished_at=finished_at,
        )
        _record_idempotent(body.idempotency_key, fingerprint, response.model_dump())
        logger.info(
            "Execution completed: action=%s bytes=%s",
            body.action.type,
            result.get("bytes", 0),
        )
        return response
    except ActionFailed as error:
        logger.error("Execution failed: action=%s status=%s", body.action.type, error.status_code)
        raise ExecutionError(error.status_code, f"Execution failed: {error.message}")
    except ExecutionError:
        raise
    except Exception:  # Never leak internals; never claim success.
        logger.error("Execution crashed: action=%s", body.action.type)
        raise ExecutionError(500, "Execution failed unexpectedly.")
    finally:
        _EXEC_LOCK.release()
