"""Phase 4 execution engine tests: approval, validation, actions, guards."""

import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import execution
from execution import (
    ExecuteRequest,
    ExecutionError,
    fingerprint_of,
    reset_engine_state,
    run_execution,
)
from main import execute_task, list_actions


def make_task(**overrides):
    task = {
        "id": "t1",
        "title": "List the rooms to empty",
        "detail": "Walk through the flat.",
        "depends_on": [],
        "success_criteria": ["Each room has a list"],
        "needs_info": [],
        "status": "ready",
    }
    task.update(overrides)
    return task


def make_action(action_type="research.summarize", params=None):
    return {"type": action_type, "params": params if params is not None else {}}


def make_approval(task, action, status="approved"):
    return {
        "status": status,
        "approved_at": "2026-01-01T00:00:00+00:00",
        "snapshot": {"task": dict(task), "action": {"type": action["type"], "params": dict(action["params"])}},
    }


def make_request(task=None, action=None, approval=None, context=None, key="test-key-0001"):
    task = task or make_task()
    action = action or make_action()
    approval = approval if approval is not None else make_approval(task, action)
    return ExecuteRequest.model_validate(
        {
            "plan_id": "plan-1",
            "task": task,
            "action": action,
            "approval": approval,
            "context_tasks": context or [],
            "idempotency_key": key,
        }
    )


class ExecutionTestCase(unittest.TestCase):
    def setUp(self):
        reset_engine_state()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        reset_engine_state()
        self.tmp.cleanup()

    def run_exec(self, body):
        return run_execution(body, output_dir=self.tmp.name)

    def assertExecutionError(self, body, status_code, text=None):
        with self.assertRaises(ExecutionError) as raised:
            self.run_exec(body)
        self.assertEqual(raised.exception.status_code, status_code)
        if text:
            self.assertIn(text, raised.exception.detail)


class ApprovalTests(ExecutionTestCase):
    def test_missing_approval_status_rejected(self):
        task = make_task()
        approval = make_approval(task, make_action(), status="pending")
        self.assertExecutionError(make_request(task=task, approval=approval), 422, "approval")

    def test_rejected_task_blocked(self):
        task = make_task()
        approval = make_approval(task, make_action(), status="rejected")
        self.assertExecutionError(make_request(task=task, approval=approval), 409, "rejected")

    def test_revoked_approval_blocked(self):
        task = make_task()
        approval = make_approval(task, make_action(), status="revoked")
        self.assertExecutionError(make_request(task=task, approval=approval), 409, "revoked")

    def test_malformed_approval_rejected(self):
        task = make_task()
        approval = make_approval(task, make_action())
        del approval["snapshot"]["action"]
        self.assertExecutionError(make_request(task=task, approval=approval), 422, "malformed")

    def test_modified_task_invalidates_approval(self):
        task = make_task()
        approval = make_approval(task, make_action())
        changed = make_task(title="List the rooms to empty and the garage")
        self.assertExecutionError(
            make_request(task=changed, approval=approval), 409, "changed since approval"
        )

    def test_modified_params_invalidate_approval(self):
        task = make_task()
        approval = make_approval(task, make_action())
        other = make_action(params={"topic": "other", "max_points": 3})
        self.assertExecutionError(
            make_request(task=task, action=other, approval=approval), 409, "changed since approval"
        )


class ReadinessTests(ExecutionTestCase):
    def test_blocked_task_refused(self):
        task = make_task(status="blocked")
        self.assertExecutionError(make_request(task=task), 409, "blocked")

    def test_finished_task_refused(self):
        for status in ("done", "skipped"):
            with self.subTest(status=status):
                task = make_task(status=status)
                self.assertExecutionError(make_request(task=task), 409, "already finished")

    def test_unknown_status_rejected(self):
        task = make_task(status="flying")
        self.assertExecutionError(make_request(task=task), 422, "cannot execute")

    def test_missing_prerequisites_refused(self):
        task = make_task(needs_info=["m1"])
        self.assertExecutionError(make_request(task=task), 422, "Missing prerequisites")

    def test_unknown_dependency_rejected(self):
        task = make_task(depends_on=["t9"], status="pending")
        self.assertExecutionError(make_request(task=task), 422, "Unknown dependency")

    def test_incomplete_dependency_blocks(self):
        task = make_task(depends_on=["t0"], status="pending")
        context = [{"id": "t0", "planning_status": "ready", "execution_status": "none"}]
        self.assertExecutionError(make_request(task=task, context=context), 409, "not completed")

    def test_failed_dependency_blocks(self):
        task = make_task(depends_on=["t0"], status="pending")
        context = [{"id": "t0", "planning_status": "ready", "execution_status": "failed"}]
        self.assertExecutionError(make_request(task=task, context=context), 409, "failed")

    def test_completed_or_locally_done_dependency_passes(self):
        for context in (
            [{"id": "t0", "execution_status": "completed"}],
            [{"id": "t0", "local": "done"}],
            [{"id": "t0", "local": "skipped"}],
        ):
            with self.subTest(context=context):
                task = make_task(depends_on=["t0"], status="pending")
                response = self.run_exec(make_request(task=task, context=context))
                self.assertEqual(response.status, "completed")


class ActionTests(ExecutionTestCase):
    def test_unsupported_action_refused(self):
        task = make_task()
        action = {"type": "email.send", "params": {}}
        approval = make_approval(task, action)
        # Approval binds an unknown type: still refused, never executed.
        self.assertExecutionError(
            make_request(task=task, action=action, approval=approval), 422, "Unsupported action"
        )

    def test_invalid_params_rejected(self):
        task = make_task()
        action = make_action("document.create", {"filename": ""})
        approval = make_approval(task, action)
        self.assertExecutionError(
            make_request(task=task, action=action, approval=approval), 422, "Invalid parameters"
        )

    def test_research_summary_marks_unverified(self):
        response = self.run_exec(make_request())
        brief = response.result["brief"]
        self.assertEqual(brief["sources"], [])
        self.assertIn("UNVERIFIED", brief["verification"])
        self.assertIn("No external sources", response.result_summary)

    def test_email_draft_never_sends(self):
        task = make_task()
        action = make_action("email.draft", {"to": "a@b.c", "subject": "Hi"})
        response = self.run_exec(make_request(task=task, action=action, approval=make_approval(task, action)))
        self.assertEqual(response.result["to"], "a@b.c")
        self.assertIn("never sends", response.result["note"].lower())

    def test_document_create_writes_file(self):
        task = make_task()
        action = make_action("document.create", {"filename": "report.md", "title": "Report"})
        response = self.run_exec(make_request(task=task, action=action, approval=make_approval(task, action), key="doc-key-01"))
        self.assertEqual(response.result["filename"], "report.md")
        self.assertGreater(response.result["bytes"], 0)
        with open(f"{self.tmp.name}/report.md", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("# Report", content)
        self.assertIn("Each room has a list", content)

    def test_existing_file_requires_confirm(self):
        task = make_task()
        action = make_action("document.create", {"filename": "dup.md"})
        approval = make_approval(task, action)
        self.run_exec(make_request(task=task, action=action, approval=approval, key="dup-key-01"))
        self.assertExecutionError(
            make_request(task=task, action=action, approval=approval, key="dup-key-02"),
            409,
            "already exists",
        )
        confirmed = make_action("document.create", {"filename": "dup.md", "confirm_overwrite": True})
        response = self.run_exec(
            make_request(task=task, action=confirmed, approval=make_approval(task, confirmed), key="dup-key-03")
        )
        self.assertEqual(response.status, "completed")

    def test_plan_organize_lists_dependencies(self):
        task = make_task(depends_on=["t0"], status="pending")
        action = make_action("plan.organize", {"filename": "plan.md"})
        context = [{"id": "t0", "execution_status": "completed"}]
        response = self.run_exec(
            make_request(task=task, action=action, approval=make_approval(task, action), context=context)
        )
        with open(f"{self.tmp.name}/plan.md", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("t0: completed", content)
        self.assertIn("- [ ]", content)

    def test_file_organize_mkdir_and_move(self):
        task = make_task()
        (open(f"{self.tmp.name}/note.md", "w", encoding="utf-8").write("hi"))
        action = make_action(
            "file.organize",
            {"operations": [{"op": "mkdir", "name": "docs"}, {"op": "move", "src": "note.md", "dest": "moved.md"}]},
        )
        response = self.run_exec(make_request(task=task, action=action, approval=make_approval(task, action)))
        self.assertEqual(len(response.result["operations"]), 2)

    def test_registry_flags(self):
        specs = {spec["type"]: spec for spec in execution.action_specs()}
        self.assertEqual(
            set(specs), {"document.create", "plan.organize", "research.summarize", "email.draft", "file.organize"}
        )
        self.assertTrue(specs["email.draft"]["safe_retry"])
        self.assertTrue(specs["research.summarize"]["safe_retry"])
        self.assertFalse(specs["document.create"]["safe_retry"])
        self.assertTrue(specs["file.organize"]["side_effects"])


class PathContainmentTests(ExecutionTestCase):
    def test_traversal_rejected(self):
        for bad in ("../evil.md", "..\\evil.md", "/abs.md", "sub/dir.md", "run.exe", ""):
            with self.subTest(name=bad):
                task = make_task()
                action = make_action("document.create", {"filename": bad or "x"})
                filename = bad if bad else "x" * 200
                action = make_action("document.create", {"filename": filename})
                approval = make_approval(task, action)
                try:
                    self.run_exec(make_request(task=task, action=action, approval=approval))
                except ExecutionError as error:
                    self.assertIn(error.status_code, (409, 422))
                else:
                    self.fail(f"unsafe filename accepted: {bad!r}")


class GuardTests(ExecutionTestCase):
    def test_duplicate_key_replays_without_reexecuting(self):
        task = make_task()
        action = make_action("document.create", {"filename": "once.md"})
        approval = make_approval(task, action)
        first = self.run_exec(make_request(task=task, action=action, approval=approval, key="idem-key-1"))
        self.assertFalse(first.duplicate)
        first_id = first.execution_id
        # Delete the artifact: a replay must NOT recreate it.
        import os

        os.unlink(f"{self.tmp.name}/once.md")
        second = self.run_exec(make_request(task=task, action=action, approval=approval, key="idem-key-1"))
        self.assertTrue(second.duplicate)
        self.assertEqual(second.execution_id, first_id)
        self.assertFalse(os.path.exists(f"{self.tmp.name}/once.md"))

    def test_key_reuse_for_other_task_conflicts(self):
        first = self.run_exec(make_request(key="shared-key"))
        self.assertFalse(first.duplicate)
        other_task = make_task(id="t2", title="Other task")
        other_action = make_action()
        body = make_request(task=other_task, action=other_action, approval=make_approval(other_task, other_action), key="shared-key")
        self.assertExecutionError(body, 409, "already used")

    def test_failed_attempt_allows_retry_with_new_key(self):
        task = make_task()
        action = make_action("document.create", {"filename": "retry.md"})
        approval = make_approval(task, action)
        with open(f"{self.tmp.name}/retry.md", "w", encoding="utf-8") as handle:
            handle.write("old")
        self.assertExecutionError(
            make_request(task=task, action=action, approval=approval, key="retry-key-1"), 409, "already exists"
        )
        confirmed = make_action("document.create", {"filename": "retry.md", "confirm_overwrite": True})
        response = self.run_exec(
            make_request(task=task, action=confirmed, approval=make_approval(task, confirmed), key="retry-key-2")
        )
        self.assertEqual(response.status, "completed")

    def test_busy_lock_rejects_concurrent_run(self):
        execution._EXEC_LOCK.acquire()
        try:
            self.assertExecutionError(make_request(), 409, "already executing")
        finally:
            execution._EXEC_LOCK.release()

    def test_handler_timeout_is_controlled(self):
        original = execution.ACTIONS["research.summarize"]["handler"]

        def slow(task, params, context, output_dir):
            time.sleep(0.5)
            return original(task, params, context, output_dir)

        execution.ACTIONS["research.summarize"]["handler"] = slow
        try:
            with patch("execution.EXECUTION_TIMEOUT_S", 0.05):
                self.assertExecutionError(make_request(), 504, "timed out")
        finally:
            execution.ACTIONS["research.summarize"]["handler"] = original

    def test_handler_crash_is_controlled(self):
        original = execution.ACTIONS["research.summarize"]["handler"]

        def broken(task, params, context, output_dir):
            raise RuntimeError("boom")

        execution.ACTIONS["research.summarize"]["handler"] = broken
        try:
            self.assertExecutionError(make_request(), 500, "unexpectedly")
        finally:
            execution.ACTIONS["research.summarize"]["handler"] = original

    def test_reset_clears_idempotency(self):
        self.run_exec(make_request(key="reset-key"))
        reset_engine_state()
        second = self.run_exec(make_request(key="reset-key"))
        self.assertFalse(second.duplicate)


class ContractTests(ExecutionTestCase):
    def test_fingerprint_stable_and_sensitive(self):
        payload = {"task": make_task(), "action": make_action()}
        self.assertEqual(fingerprint_of(payload), fingerprint_of(json_loads_dump(payload)))
        changed = {"task": make_task(title="Other"), "action": make_action()}
        self.assertNotEqual(fingerprint_of(payload), fingerprint_of(changed))

    def test_response_carries_history_fields(self):
        response = self.run_exec(make_request())
        self.assertRegex(response.execution_id, r"^[0-9a-f]+$")
        self.assertRegex(response.fingerprint, r"^[0-9a-f]{64}$")
        self.assertTrue(response.started_at < response.finished_at or response.started_at == response.finished_at)
        self.assertTrue(response.result_summary)
        self.assertEqual(response.action_type, "research.summarize")

    def test_request_validation_rejects_garbage(self):
        with self.assertRaises(ValidationError):
            ExecuteRequest.model_validate({"plan_id": "p"})
        with self.assertRaises(ValidationError):
            ExecuteRequest.model_validate(
                {
                    "plan_id": "p",
                    "task": make_task(),
                    "action": make_action(),
                    "approval": make_approval(make_task(), make_action()),
                    "context_tasks": [],
                    "idempotency_key": "short",
                }
            )

    def test_route_maps_errors_to_http(self):
        task = make_task(status="blocked")
        with self.assertRaises(HTTPException) as raised:
            execute_task(make_request(task=task))
        self.assertEqual(raised.exception.status_code, 409)

    def test_route_success_and_actions_listing(self):
        response = execute_task(make_request(key="route-key-1"))
        self.assertEqual(response.status, "completed")
        listing = list_actions()
        self.assertEqual(len(listing["actions"]), 5)


def json_loads_dump(payload):
    import json

    return json.loads(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
