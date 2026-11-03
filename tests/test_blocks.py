"""Tests hors-ligne de la validation des blocs de présentation.

Aucun appel à Meta Model API : seule la logique pure de main.py est exercée
(normalisation par type, nettoyage, secours, diagnostic et compatibilité).
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import unittest

from main import (
    Block,
    PlanResponse,
    diagnose_blocks,
    finalize_plan,
    normalize_block_content,
    rescue_procedure,
    sanitize_blocks,
)
from pydantic import ValidationError


class NormalizeTest(unittest.TestCase):
    def test_unknown_type_rejected(self):
        self.assertIsNone(normalize_block_content("kanban", {"items": ["a"]}))
        self.assertIsNone(normalize_block_content("table", ["not", "a", "dict"]))
        self.assertIsNone(normalize_block_content(None, {}))

    def test_checklist(self):
        self.assertEqual(
            normalize_block_content("checklist", {"items": ["  A  ", "", 42, None]}),
            {"items": ["A"]},
        )
        self.assertIsNone(normalize_block_content("checklist", {"items": []}))
        self.assertIsNone(normalize_block_content("checklist", {}))

    def test_table(self):
        content = {"headers": ["A", "B"], "rows": [["1", "2"], {"x": "3", "y": "4"}, "nope", []]}
        normalized = normalize_block_content("table", content)
        self.assertEqual(normalized["headers"], ["A", "B"])
        self.assertEqual(normalized["rows"], [["1", "2"], ["3", "4"]])

    def test_table_requires_headers_and_rows(self):
        self.assertIsNone(normalize_block_content("table", {"headers": ["A"], "rows": []}))
        self.assertIsNone(normalize_block_content("table", {"headers": [], "rows": [["1"]]}))
        self.assertIsNone(normalize_block_content("table", {"headers": "A", "rows": "B"}))

    def test_comparison_table(self):
        # Miroir du cas réel : comparatif React / Vue / Angular, 7 colonnes.
        headers = ["Framework", "Avantages", "Inconvénients", "Facilité", "Performance", "Écosystème", "Outils"]
        rows = [
            ["React", "Écosystème vaste", "Courbe raide", "Moyenne", "Bonne", "Large", "Vite"],
            ["Vue", "Simple", "Moins d'emplois", "Facile", "Bonne", "Correct", "Vite"],
            ["Angular", "Complet", "Verbeux", "Difficile", "Bonne", "Google", "CLI"],
        ]
        normalized = normalize_block_content("table", {"headers": headers, "rows": rows})
        self.assertEqual(len(normalized["headers"]), 7)
        self.assertEqual(len(normalized["rows"]), 3)
        self.assertTrue(all(len(row) == 7 for row in normalized["rows"]))

    def test_tree(self):
        content = {"nodes": ["Racine", {"label": "  Branche ", "children": [{"label": "Feuille"}]}, 42]}
        normalized = normalize_block_content("tree", content)
        self.assertEqual(len(normalized["nodes"]), 2)
        self.assertEqual(normalized["nodes"][0], {"label": "Racine"})
        self.assertEqual(normalized["nodes"][1]["children"], [{"label": "Feuille"}])

    def test_timeline(self):
        normalized = normalize_block_content(
            "timeline", {"events": ["Départ", {"label": "", "detail": "Milieu"}]}
        )
        self.assertEqual(normalized["events"][0]["label"], "Départ")
        self.assertEqual(normalized["events"][1]["label"], "Étape 2")

    def test_decision(self):
        content = {
            "options": [
                {"name": "A", "pros": ["+1"], "cons": []},
                {"name": ""},
                {"name": "B"},
            ],
            "recommendation": " A ",
        }
        normalized = normalize_block_content("decision", content)
        # L'option sans nom et l'option sans arguments sont écartées.
        self.assertEqual(len(normalized["options"]), 1)
        self.assertEqual(normalized["recommendation"], "A")

    def test_procedure(self):
        normalized = normalize_block_content(
            "procedure", {"steps": ["Faire X", {"title": "  Y ", "detail": "zt"}]}
        )
        self.assertEqual(normalized["steps"][0], {"title": "Faire X", "detail": ""})

    def test_schedule(self):
        normalized = normalize_block_content(
            "schedule", {"days": [{"label": "Jour 1", "tasks": ["a", 1]}, {"label": ""}]}
        )
        self.assertEqual(normalized["days"], [{"label": "Jour 1", "tasks": ["a"]}])

    def test_summary(self):
        self.assertIsNone(normalize_block_content("summary", {}))
        normalized = normalize_block_content("summary", {"points": ["p"], "conclusion": " c "})
        self.assertEqual(normalized, {"points": ["p"], "conclusion": "c"})


class SanitizeTest(unittest.TestCase):
    def test_mixed_blocks(self):
        raw = [
            {"type": "checklist", "title": "T", "content": {"items": ["a"]}},
            {"type": "unknown", "content": {}},
            "nope",
            {"type": "table", "content": {"headers": [], "rows": []}},
        ]
        result = sanitize_blocks(raw)
        self.assertEqual([b["type"] for b in result], ["checklist"])

    def test_malformed_input(self):
        self.assertEqual(sanitize_blocks("nope"), [])
        self.assertEqual(sanitize_blocks(None), [])
        self.assertEqual(sanitize_blocks([None, 42, []]), [])

    def test_cap(self):
        raw = [{"type": "summary", "content": {"points": ["p"]}} for _ in range(10)]
        self.assertEqual(len(sanitize_blocks(raw)), 4)


class RescueTest(unittest.TestCase):
    def test_rescue_procedure(self):
        result = rescue_procedure(["  Première  ", "", None])
        self.assertEqual(result["type"], "procedure")
        self.assertEqual(result["content"]["steps"], [{"title": "Première", "detail": ""}])

    def test_no_rescue_without_steps(self):
        self.assertIsNone(rescue_procedure([]))
        self.assertIsNone(rescue_procedure(["   ", None, 42]))
        self.assertIsNone(rescue_procedure("nope"))

    def test_rescue_caps_steps(self):
        result = rescue_procedure([f"Étape {i}" for i in range(25)])
        self.assertEqual(len(result["content"]["steps"]), 20)


class DiagnoseTest(unittest.TestCase):
    def test_missing_blocks(self):
        self.assertEqual(diagnose_blocks(None), {"reçus": 0, "forme": "NoneType"})

    def test_mixed_types(self):
        result = diagnose_blocks([{"type": "table"}, "x", {"nottype": 1}])
        self.assertEqual(result["reçus"], 3)
        self.assertEqual(result["types"], ["table", "str", None])


class ContractTest(unittest.TestCase):
    def test_old_shape_compatible(self):
        plan = PlanResponse(title="T", steps=["a"], questions=[])
        self.assertEqual(plan.blocks, [])

    def test_block_rejects_unknown_type(self):
        with self.assertRaises(ValidationError):
            Block(type="kanban", title="", content={})


class FinalizeTest(unittest.TestCase):
    def test_preserves_table_and_decision_blocks(self):
        # Régression : les blocs validés doivent survivre à l'assemblage final.
        plan = PlanResponse(
            title="  T  ",
            steps=["  a  ", "b"],
            questions=["  q ?  "],
            blocks=[
                {"type": "table", "title": "C", "content": {"headers": ["H"], "rows": [["x"]]}},
                {
                    "type": "decision",
                    "title": "",
                    "content": {"options": [{"name": "A", "pros": ["p"], "cons": []}]},
                },
            ],
        )
        final = finalize_plan(plan)
        self.assertEqual([b.type for b in final.blocks], ["table", "decision"])
        self.assertEqual(final.title, "T")
        self.assertEqual(final.steps, ["a", "b"])
        self.assertEqual(final.questions, ["q ?"])

    def test_rejects_blank_content(self):
        base = {"title": "T", "steps": ["a"], "questions": [], "blocks": []}
        with self.assertRaises(ValueError):
            finalize_plan(PlanResponse(**{**base, "title": "   "}))
        with self.assertRaises(ValueError):
            finalize_plan(PlanResponse(**{**base, "steps": ["   "]}))

    def test_rejects_over_limits(self):
        # Assignation directe : Pydantic v2 applique déjà ces limites à la construction.
        plan = PlanResponse(title="T", steps=["a"], questions=[])
        plan.steps = [f"s{i}" for i in range(21)]
        with self.assertRaises(ValueError):
            finalize_plan(plan)
        plan.steps = ["a"]
        plan.questions = [f"q{i}" for i in range(11)]
        with self.assertRaises(ValueError):
            finalize_plan(plan)


class ArticleTest(unittest.TestCase):
    def test_valid_article(self):
        content = {
            "introduction": "  Intro complète.  ",
            "sections": [
                {"heading": "  Argument 1  ", "paragraphs": ["  Paragraphe rédigé.  ", ""]},
                {"heading": "Argument 2", "paragraphs": ["Deuxième paragraphe."]},
            ],
            "conclusion": "  Conclusion complète.  ",
        }
        normalized = normalize_block_content("article", content)
        self.assertEqual(normalized["introduction"], "Intro complète.")
        self.assertEqual(len(normalized["sections"]), 2)
        self.assertEqual(normalized["sections"][0]["paragraphs"], ["Paragraphe rédigé."])
        self.assertEqual(normalized["conclusion"], "Conclusion complète.")

    def test_invalid_article(self):
        good_section = {"heading": "H", "paragraphs": ["Un paragraphe rédigé."]}
        self.assertIsNone(normalize_block_content("article", {}))
        self.assertIsNone(
            normalize_block_content(
                "article",
                {"introduction": "", "sections": [good_section], "conclusion": "C"},
            )
        )
        self.assertIsNone(
            normalize_block_content(
                "article", {"introduction": "I", "sections": [], "conclusion": "C"}
            )
        )
        self.assertIsNone(
            normalize_block_content(
                "article",
                {
                    "introduction": "I",
                    "sections": [{"heading": "", "paragraphs": ["p"]}],
                    "conclusion": "C",
                },
            )
        )
        self.assertIsNone(
            normalize_block_content(
                "article",
                {"introduction": "I", "sections": [good_section], "conclusion": "   "},
            )
        )

    def test_article_block_validates(self):
        block = Block(type="article", title="T", content={"introduction": "I"})
        self.assertEqual(block.type, "article")
        plan = PlanResponse(
            title="T",
            steps=["Structure : introduction"],
            questions=[],
            blocks=[{"type": "article", "title": "", "content": {"introduction": "I"}}],
        )
        self.assertEqual(plan.blocks[0].type, "article")


class ExistingTypesPreservedTest(unittest.TestCase):
    def test_all_eight_types_still_normalize(self):
        cases = {
            "checklist": {"items": ["a"]},
            "table": {"headers": ["H"], "rows": [["x"]]},
            "tree": {"nodes": [{"label": "N"}]},
            "timeline": {"events": [{"label": "E"}]},
            "decision": {"options": [{"name": "A", "pros": ["p"], "cons": []}]},
            "procedure": {"steps": [{"title": "T"}]},
            "schedule": {"days": [{"label": "J1", "tasks": ["a"]}]},
            "summary": {"points": ["p"]},
        }
        for block_type, content in cases.items():
            with self.subTest(block_type=block_type):
                self.assertIsNotNone(normalize_block_content(block_type, content))


if __name__ == "__main__":
    unittest.main()
