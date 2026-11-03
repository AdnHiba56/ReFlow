"""Tests ciblés du moteur de planification (schémas, graphe, route).

Aucun appel à Meta Model API : le client est entièrement simulé.
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_plan.py -v
"""

import json
import unittest
from unittest.mock import patch

import httpx2
from fastapi import HTTPException
from openai import APITimeoutError, AuthenticationError
from pydantic import ValidationError

from main import (
    AgentPlanRequest,
    AgentPlanResponse,
    _build_agent_plan,
    create_structured_plan,
)


def valid_plan_dict():
    return {
        "goal": "Préparer un déménagement",
        "summary": "Trier puis réserver le transport.",
        "tasks": [
            {
                "id": "t1",
                "title": "Lister les pièces à vider",
                "detail": "",
                "depends_on": [],
                "success_criteria": ["Chaque pièce a une liste"],
                "needs_info": [],
            },
            {
                "id": "t2",
                "title": "Réserver un camion",
                "detail": "",
                "depends_on": ["t1"],
                "success_criteria": ["Camion réservé à une date"],
                "needs_info": [],
            },
        ],
        "missing_information": [],
        "questions": [],
        "blocks": [],
    }


class FakeResponse:
    def __init__(self, output_text=None):
        self.output_text = output_text


class FakeResponses:
    def __init__(self, outputs=None, error=None):
        self.outputs = list(outputs or [])
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if not self.outputs:
            raise AssertionError("appel modèle inattendu")
        return FakeResponse(self.outputs.pop(0))


class FakeClient:
    def __init__(self, outputs=None, error=None):
        self.responses = FakeResponses(outputs=outputs, error=error)


class AgentPlanBuilderTest(unittest.TestCase):
    def test_valid_plan_derives_statuses(self):
        plan = _build_agent_plan(valid_plan_dict(), 10)
        self.assertIsInstance(plan, AgentPlanResponse)
        self.assertEqual(plan.goal, "Préparer un déménagement")
        self.assertEqual(plan.status.value, "ready")
        by_id = {task.id: task for task in plan.tasks}
        self.assertEqual(by_id["t1"].status.value, "ready")
        self.assertEqual(by_id["t2"].status.value, "pending")
        self.assertEqual(by_id["t2"].needs_info, [])

    def test_blocked_status_and_needs_info_autofill(self):
        data = valid_plan_dict()
        data["missing_information"] = [
            {
                "id": "m1",
                "field": "date_demenagement",
                "question": "Quelle est la date du déménagement ?",
                "blocks_task_ids": ["t2"],
            }
        ]
        plan = _build_agent_plan(data, 10)
        self.assertEqual(plan.status.value, "needs_info")
        by_id = {task.id: task for task in plan.tasks}
        self.assertEqual(by_id["t2"].status.value, "blocked")
        self.assertEqual(by_id["t2"].needs_info, ["m1"])
        self.assertEqual(by_id["t1"].status.value, "ready")

    def test_model_status_is_recomputed(self):
        data = valid_plan_dict()
        data["tasks"][0]["status"] = "done"
        plan = _build_agent_plan(data, 10)
        by_id = {task.id: task for task in plan.tasks}
        self.assertEqual(by_id["t1"].status.value, "ready")

    def test_duplicate_task_ids_rejected(self):
        data = valid_plan_dict()
        data["tasks"][1]["id"] = "t1"
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_unknown_dependency_rejected(self):
        data = valid_plan_dict()
        data["tasks"][1]["depends_on"] = ["t9"]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_self_dependency_rejected(self):
        data = valid_plan_dict()
        data["tasks"][0]["depends_on"] = ["t1"]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_cycle_rejected(self):
        data = valid_plan_dict()
        data["tasks"][0]["depends_on"] = ["t2"]
        data["tasks"][1]["depends_on"] = ["t1"]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_empty_criteria_rejected(self):
        data = valid_plan_dict()
        data["tasks"][0]["success_criteria"] = ["   "]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_missing_goal_rejected(self):
        data = valid_plan_dict()
        data["goal"] = "   "
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_missing_tasks_rejected(self):
        for tasks in (None, [], "t1"):
            with self.subTest(tasks=tasks):
                data = valid_plan_dict()
                data["tasks"] = tasks
                with self.assertRaises(ValueError):
                    _build_agent_plan(data, 10)

    def test_non_dict_rejected(self):
        for data in (None, [], "plan"):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    _build_agent_plan(data, 10)

    def test_too_many_tasks_rejected(self):
        with self.assertRaises(ValueError):
            _build_agent_plan(valid_plan_dict(), 1)

    def test_unknown_blocked_task_rejected(self):
        data = valid_plan_dict()
        data["missing_information"] = [
            {"id": "m1", "field": "date", "question": "Quand ?", "blocks_task_ids": ["t9"]}
        ]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_unknown_needs_info_ref_rejected(self):
        data = valid_plan_dict()
        data["tasks"][0]["needs_info"] = ["m9"]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_duplicate_missing_ids_rejected(self):
        data = valid_plan_dict()
        info = {"id": "m1", "field": "date", "question": "Quand ?", "blocks_task_ids": []}
        data["missing_information"] = [info, dict(info)]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_too_many_questions_rejected(self):
        data = valid_plan_dict()
        data["questions"] = ["q1", "q2", "q3", "q4", "q5", "q6"]
        with self.assertRaises(ValueError):
            _build_agent_plan(data, 10)

    def test_none_lists_normalized(self):
        data = valid_plan_dict()
        data["missing_information"] = None
        data["questions"] = None
        data["blocks"] = None
        plan = _build_agent_plan(data, 10)
        self.assertEqual(plan.missing_information, [])
        self.assertEqual(plan.questions, [])
        self.assertEqual(plan.blocks, [])
        self.assertEqual(plan.status.value, "ready")

    def test_blocks_sanitized(self):
        data = valid_plan_dict()
        data["blocks"] = [
            {"type": "checklist", "title": "Suivi", "content": {"items": ["a", "b"]}},
            {"type": "inconnu", "title": "x", "content": {}},
        ]
        plan = _build_agent_plan(data, 10)
        self.assertEqual(len(plan.blocks), 1)
        self.assertEqual(plan.blocks[0].type, "checklist")


class StructuredRouteTest(unittest.TestCase):
    def test_success_first_try(self):
        fake = FakeClient(outputs=[json.dumps(valid_plan_dict())])
        with patch("main.get_client", return_value=fake):
            plan = create_structured_plan(AgentPlanRequest(request="Je déménage."))
        self.assertIsInstance(plan, AgentPlanResponse)
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(len(fake.responses.calls), 1)
        call = fake.responses.calls[0]
        self.assertEqual(call["model"], "muse-spark-1.3")
        self.assertEqual(call["input"], "Je déménage.")
        self.assertIn("at most 10", call["instructions"])

    def test_max_tasks_forwarded(self):
        fake = FakeClient(outputs=[json.dumps(valid_plan_dict())])
        with patch("main.get_client", return_value=fake):
            create_structured_plan(AgentPlanRequest(request="Je déménage.", max_tasks=3))
        self.assertIn("at most 3", fake.responses.calls[0]["instructions"])

    def test_repair_then_success(self):
        fake = FakeClient(outputs=["pas du json", json.dumps(valid_plan_dict())])
        with patch("main.get_client", return_value=fake):
            plan = create_structured_plan(AgentPlanRequest(request="Je déménage."))
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(len(fake.responses.calls), 2)
        self.assertIn("IMPORTANT CORRECTION", fake.responses.calls[1]["instructions"])

    def test_invalid_twice_is_502(self):
        data = valid_plan_dict()
        data["tasks"][1]["depends_on"] = ["t9"]
        fake = FakeClient(outputs=[json.dumps(data), json.dumps(data)])
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_structured_plan(AgentPlanRequest(request="Je déménage."))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(len(fake.responses.calls), 2)

    def test_empty_output_is_502_without_repair(self):
        fake = FakeClient(outputs=["   "])
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_structured_plan(AgentPlanRequest(request="Je déménage."))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(len(fake.responses.calls), 1)

    def test_auth_error_maps_401(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        error = AuthenticationError(
            "clé invalide", response=httpx2.Response(401, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_structured_plan(AgentPlanRequest(request="Je déménage."))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_timeout_maps_504(self):
        error = APITimeoutError(request=httpx2.Request("POST", "https://api.meta.ai/v1/responses"))
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_structured_plan(AgentPlanRequest(request="Je déménage."))
        self.assertEqual(ctx.exception.status_code, 504)

    def test_empty_request_is_422(self):
        body = AgentPlanRequest(request="   ")
        with self.assertRaises(HTTPException) as ctx:
            create_structured_plan(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_request_limits_rejected(self):
        with self.assertRaises(ValidationError):
            AgentPlanRequest(request="")
        with self.assertRaises(ValidationError):
            AgentPlanRequest(request="x" * 2001)
        with self.assertRaises(ValidationError):
            AgentPlanRequest(request="ok", max_tasks=0)
        with self.assertRaises(ValidationError):
            AgentPlanRequest(request="ok", max_tasks=21)


if __name__ == "__main__":
    unittest.main()
