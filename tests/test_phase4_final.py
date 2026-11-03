"""Phase 4 finalisation: manual tracking vs real execution + frontend gates.

Backend contract (this file):
- A manual Done/Skipped marker (local tracking) never blocks execution: the
  backend does not even receive it on the executed task, and a locally marked
  dependency still satisfies readiness (documented single-user behaviour).
- ``email.draft`` only prepares a draft: no sending primitive exists.
- Planning ``done``/``skipped`` statuses keep their existing refusal.

Frontend gates (same file, real ``frontend/src/lib/execution.js`` driven by
node): ``readiness()`` must ignore manual tracking, stay consistent with the
backend on planning statuses, and record history events that survive a
save/load round-trip.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from execution import ExecuteRequest, ExecutionError, reset_engine_state, run_execution

ROOT = Path(__file__).resolve().parent.parent
EXEC_JS = ROOT / "frontend" / "src" / "lib" / "execution.js"


def make_task(**overrides):
    task = {
        "id": "t5",
        "title": "Draft invitation email for a friend",
        "detail": "Invite a friend for dinner.",
        "depends_on": [],
        "success_criteria": ["Draft exists"],
        "needs_info": [],
        "status": "ready",
    }
    task.update(overrides)
    return task


def make_request(task, action_type="email.draft", params=None, context=None, key="phase4-key-1"):
    params = {"to": "", "subject": "", "tone": "neutral"} if params is None else params
    action = {"type": action_type, "params": params}
    return ExecuteRequest.model_validate(
        {
            "plan_id": "plan-1",
            "task": task,
            "action": action,
            "approval": {
                "status": "approved",
                "approved_at": "2026-01-01T00:00:00+00:00",
                "snapshot": {"task": dict(task), "action": {"type": action_type, "params": dict(params)}},
            },
            "context_tasks": context or [],
            "idempotency_key": key,
        }
    )


class ManualTrackingBackendTests(unittest.TestCase):
    """Manual Done/Skipped must never block a never-executed task (backend side)."""

    def setUp(self):
        reset_engine_state()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        reset_engine_state()
        self.tmp.cleanup()

    def test_manually_done_task_still_executes(self):
        # The executed task carries its planning status; the manual marker
        # only exists in the dependency context, where it must not block.
        task = make_task()
        context = [{"id": "t5", "planning_status": "done", "execution_status": "none", "local": "done"}]
        response = run_execution(make_request(task, context=context), output_dir=self.tmp.name)
        self.assertEqual(response.status, "completed")

    def test_manually_skipped_task_still_executes(self):
        task = make_task()
        context = [{"id": "t5", "planning_status": "skipped", "execution_status": "none", "local": "skipped"}]
        response = run_execution(make_request(task, context=context, key="phase4-key-2"), output_dir=self.tmp.name)
        self.assertEqual(response.status, "completed")

    def test_task_payload_has_no_local_marker(self):
        # Contract proof: even if a client sends a manual marker on the task
        # itself, the schema drops it and execution proceeds on the planning
        # status only. The approval snapshot is built from the known fields,
        # exactly as the frontend does.
        clean = make_task()
        task = dict(clean)
        task["local"] = "done"
        body = ExecuteRequest.model_validate(
            {
                "plan_id": "plan-1",
                "task": task,
                "action": {"type": "email.draft", "params": {"to": "", "subject": "", "tone": "neutral"}},
                "approval": {
                    "status": "approved",
                    "approved_at": "2026-01-01T00:00:00+00:00",
                    "snapshot": {
                        "task": clean,
                        "action": {"type": "email.draft", "params": {"to": "", "subject": "", "tone": "neutral"}},
                    },
                },
                "context_tasks": [],
                "idempotency_key": "phase4-key-3",
            }
        )
        self.assertNotIn("local", body.task.model_dump())
        response = run_execution(body, output_dir=self.tmp.name)
        self.assertEqual(response.status, "completed")


class EmailDraftSafetyTests(unittest.TestCase):
    def setUp(self):
        reset_engine_state()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        reset_engine_state()
        self.tmp.cleanup()

    def test_email_draft_prepares_without_sending(self):
        task = make_task()
        response = run_execution(
            make_request(task, params={"to": "friend@example.com", "subject": "Dinner?", "tone": "friendly"}),
            output_dir=self.tmp.name,
        )
        self.assertEqual(response.status, "completed")
        self.assertIn("Not sent", response.result_summary)
        self.assertEqual(response.result["to"], "friend@example.com")
        self.assertIn("never sends", response.result["note"].lower())
        self.assertNotIn("sent", [key.lower() for key in response.result])

    def test_engine_has_no_network_or_send_primitive(self):
        source = (ROOT / "execution.py").read_text(encoding="utf-8")
        for token in (
            "smtplib",
            "sendmail",
            "SMTP(",
            "socket",
            "requests",
            "urllib",
            "http.client",
            "subprocess",
            "os.system",
            "shutil",
            "rmtree",
            "rmdir",
        ):
            self.assertNotIn(token, source, f"forbidden primitive in execution engine: {token}")
        self.assertIn("ReFlow never sends emails", source)


@unittest.skipIf(shutil.which("node") is None, "node is required to drive the real execution.js")
class FrontendExecutionGateTests(unittest.TestCase):
    """Runs the REAL frontend/src/lib/execution.js under node.

    Only the ``./agentPlans.js`` import is stubbed (the 3-line
    ``effectiveStatus`` helper, copied verbatim), because its module graph
    pulls JSX that node cannot parse. Every function under test — readiness,
    approvals, store, history — is the byte-identical project code.
    """

    HARNESS_TAIL = r"""
import assert from "node:assert";

globalThis.localStorage = {
  _data: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._data, k) ? this._data[k] : null; },
  setItem(k, v) { this._data[k] = String(v); },
  removeItem(k) { delete this._data[k]; },
  clear() { this._data = {}; },
};

const TASK = {
  id: "t5",
  title: "Draft invitation email for a friend",
  detail: "Invite a friend for dinner.",
  depends_on: [],
  success_criteria: ["Draft exists"],
  needs_info: [],
  status: "ready",
};
const PLAN_ID = "plan-1";
const ACTION = "email.draft";
const PARAMS = { to: "", subject: "", tone: "neutral" };
const entryFor = (task, localStatus = {}, tasks = null) => ({
  id: PLAN_ID,
  tasks: tasks || [task],
  localStatus,
});
const approvedStore = (task, params = PARAMS) =>
  approveTaskState(emptyExecutionStore(), PLAN_ID, task, task.title, ACTION, params);

// 1. Manual Done on a never-executed task must NOT block Execute.
{
  const store = approvedStore(TASK);
  const entry = entryFor(TASK, { t5: "done" });
  const ready = readiness(store, entry, TASK, ACTION, PARAMS, {});
  assert.equal(ready.ok, true, "manual Done blocks Execute: " + JSON.stringify(ready.reasons));
}
// 2. Manual Skipped must NOT block Execute either.
{
  const store = approvedStore(TASK);
  const entry = entryFor(TASK, { t5: "skipped" });
  const ready = readiness(store, entry, TASK, ACTION, PARAMS, {});
  assert.equal(ready.ok, true, "manual Skipped blocks Execute: " + JSON.stringify(ready.reasons));
}
// 3. After unchecking (no local marker), the task stays executable.
{
  const store = approvedStore(TASK);
  const ready = readiness(store, entryFor(TASK), TASK, ACTION, PARAMS, {});
  assert.equal(ready.ok, true, "unchecked task blocked: " + JSON.stringify(ready.reasons));
}
// 4. Planning done/skipped (server status) still blocks, like the backend.
for (const status of ["done", "skipped"]) {
  const t = { ...TASK, status };
  const store = approvedStore(t);
  const ready = readiness(store, entryFor(t), t, ACTION, PARAMS, {});
  assert.equal(ready.ok, false, `planning ${status} should block`);
  assert.ok(
    ready.reasons.some((r) => r.includes("already finished")),
    "planning done must report already finished: " + JSON.stringify(ready.reasons)
  );
}
// 5. A really executed task must not run twice.
{
  let store = approvedStore(TASK);
  store = startExecutionState(store, PLAN_ID, TASK, ACTION, "key-1");
  store = completeExecutionState(store, PLAN_ID, "t5", TASK.title, {
    execution_id: "abc123",
    result_summary: "Email draft prepared. Not sent.",
    result: { subject: "Dinner?" },
    fingerprint: "f".repeat(64),
    started_at: "2026-01-01T00:00:00+00:00",
    finished_at: "2026-01-01T00:00:01+00:00",
  });
  const ready = readiness(store, entryFor(TASK), TASK, ACTION, PARAMS, {});
  assert.equal(ready.ok, false, "completed task should block");
  assert.ok(ready.reasons.some((r) => r.includes("Already executed")));
}
// 6. A failed task can be retried (backend guards stay authoritative).
{
  let store = approvedStore(TASK);
  store = startExecutionState(store, PLAN_ID, TASK, ACTION, "key-1");
  store = failExecutionState(store, PLAN_ID, "t5", TASK.title, "boom");
  const ready = readiness(store, entryFor(TASK), TASK, ACTION, PARAMS, {});
  assert.equal(ready.ok, true, "failed task should allow retry: " + JSON.stringify(ready.reasons));
}
// 7. Dependencies: real completion or manual prereq unblocks; missing blocks.
{
  const dep = { ...TASK, id: "t1", title: "Pick a date", depends_on: [] };
  const next = { ...TASK, id: "t2", depends_on: ["t1"] };
  const tasks = [dep, next];
  const params = PARAMS;
  // 7a. Dependency really executed.
  let store = approveTaskState(emptyExecutionStore(), PLAN_ID, next, next.title, ACTION, params);
  store = {
    ...store,
    executions: { [`${PLAN_ID}:t1`]: { status: "completed", requested_at: "2026-01-01T00:00:00+00:00" } },
  };
  let ready = readiness(store, { id: PLAN_ID, tasks, localStatus: {} }, next, ACTION, params, {});
  assert.equal(ready.ok, true, "executed dep should unblock: " + JSON.stringify(ready.reasons));
  // 7b. Dependency done by hand (documented single-user behaviour).
  store = approveTaskState(emptyExecutionStore(), PLAN_ID, next, next.title, ACTION, params);
  ready = readiness(store, { id: PLAN_ID, tasks, localStatus: { t1: "done" } }, next, ACTION, params, {});
  assert.equal(ready.ok, true, "manual dep should unblock: " + JSON.stringify(ready.reasons));
  // 7c. Dependency not started.
  store = approveTaskState(emptyExecutionStore(), PLAN_ID, next, next.title, ACTION, params);
  ready = readiness(store, { id: PLAN_ID, tasks, localStatus: {} }, next, ACTION, params, {});
  assert.equal(ready.ok, false, "missing dep should block");
  assert.ok(ready.reasons.some((r) => r.includes("t1") && r.includes("not completed")));
  // 7d. Unknown dependency id.
  const bad = { ...TASK, id: "t2", depends_on: ["t9"] };
  store = approveTaskState(emptyExecutionStore(), PLAN_ID, bad, bad.title, ACTION, params);
  ready = readiness(store, { id: PLAN_ID, tasks: [bad], localStatus: {} }, bad, ACTION, params, {});
  assert.equal(ready.ok, false, "unknown dep should block");
}
// 8. Approvals: required, stale on param change, revoked/rejected block.
{
  const params = PARAMS;
  const fresh = approvedStore(TASK, params);
  assert.equal(approvalState(fresh, PLAN_ID, TASK, ACTION, params).state, "approved");
  assert.equal(approvalState(fresh, PLAN_ID, TASK, ACTION, { ...params, subject: "Other" }).state, "stale");
  let ready = readiness(fresh, entryFor(TASK), TASK, ACTION, { ...params, subject: "Other" }, {});
  assert.equal(ready.ok, false, "stale approval should block");
  assert.equal(approvalState(emptyExecutionStore(), PLAN_ID, TASK, ACTION, params).state, "none");
  ready = readiness(emptyExecutionStore(), entryFor(TASK), TASK, ACTION, params, {});
  assert.equal(ready.ok, false, "missing approval should block");
  const revoked = revokeTaskState(fresh, PLAN_ID, "t5", TASK.title);
  assert.equal(approvalState(revoked, PLAN_ID, TASK, ACTION, params).state, "none");
  ready = readiness(revoked, entryFor(TASK), TASK, ACTION, params, {});
  assert.equal(ready.ok, false, "revoked approval should block");
  const rejected = rejectTaskState(emptyExecutionStore(), PLAN_ID, "t5", TASK.title);
  assert.equal(approvalState(rejected, PLAN_ID, TASK, ACTION, params).state, "rejected");
  ready = readiness(rejected, entryFor(TASK), TASK, ACTION, params, {});
  assert.equal(ready.ok, false, "rejected task should block");
}
// 9. History: every attempt leaves task/action/result/status/time, and the
// store survives a save/load round-trip under the existing key.
{
  let store = emptyExecutionStore();
  store = approveTaskState(store, PLAN_ID, TASK, TASK.title, ACTION, PARAMS);
  store = startExecutionState(store, PLAN_ID, TASK, ACTION, "key-9");
  store = completeExecutionState(store, PLAN_ID, "t5", TASK.title, {
    execution_id: "abc123",
    result_summary: "Email draft prepared. Not sent.",
    result: {},
    fingerprint: "f".repeat(64),
    started_at: "2026-01-01T00:00:00+00:00",
    finished_at: "2026-01-01T00:00:01+00:00",
  });
  store = failExecutionState(store, PLAN_ID, "t5", TASK.title, "boom");
  const kinds = store.history.map((e) => e.event);
  assert.deepEqual(kinds, ["approved", "started", "completed", "failed"]);
  for (const event of store.history) {
    assert.equal(event.task_id, "t5");
    assert.ok(event.task_title);
    assert.ok(event.ts);
  }
  assert.ok(store.history[2].detail.includes("Not sent"));
  assert.equal(store.history[3].detail, "boom");
  // Manual tracking alone never writes history or executions.
  assert.deepEqual(emptyExecutionStore().history, []);
  assert.deepEqual(emptyExecutionStore().executions, {});
  saveExecutionStore(store);
  assert.equal(EXEC_STORAGE_KEY, "reflow:execution:v1", "storage key must be preserved");
  const raw = globalThis.localStorage.getItem(EXEC_STORAGE_KEY);
  assert.ok(raw && raw.includes("approved"), "store must persist under reflow:execution:v1");
  const reloaded = loadExecutionStore();
  assert.deepEqual(
    reloaded.history.map((e) => e.event),
    kinds,
    "history must survive reload"
  );
  // Crash/reload during a run converts running into interrupted.
  let running = startExecutionState(emptyExecutionStore(), PLAN_ID, TASK, ACTION, "key-10");
  saveExecutionStore(running);
  running = loadExecutionStore();
  assert.equal(running.executions[`${PLAN_ID}:t5`].status, "interrupted");
}

console.log("FRONTEND_PHASE4_OK");
"""

    EFFECTIVE_STATUS_STUB = (
        'const TASK_STATUSES = ["pending", "ready", "blocked", "done", "skipped"];\n'
        "function effectiveStatus(task, entry) {\n"
        "  const local = entry?.localStatus?.[task.id];\n"
        '  if (local === "done" || local === "skipped") return local;\n'
        "  return TASK_STATUSES.includes(task.status) ? task.status : \"pending\";\n"
        "}\n"
    )

    def test_frontend_gates_and_history(self):
        source = EXEC_JS.read_text(encoding="utf-8")
        import_line = 'import { effectiveStatus } from "./agentPlans.js";'
        self.assertIn(import_line, source, "execution.js import contract changed")
        self.assertEqual(source.count("agentPlans.js"), 1, "unexpected new imports in execution.js")
        harness = source.replace(import_line, self.EFFECTIVE_STATUS_STUB) + self.HARNESS_TAIL
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "phase4_frontend.mjs"
            script.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(script)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(ROOT),
            )
        self.assertEqual(
            completed.returncode,
            0,
            f"frontend gates failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )
        self.assertIn("FRONTEND_PHASE4_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
