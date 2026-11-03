"""Message editing and persisted image references (frontend logic).

The pure conversation helpers (real ``frontend/src/lib/conversations.js``,
driven by node) are tested for: in-place text edits of the first, middle and
last user messages, refusal of invalid edits, reply replacement without
touching later messages, history truncation for regeneration, and
save/load round-trips that keep stable media references while dropping
ephemeral ``blob:`` data.

A second test pins the critical invariant statically: the resend flow in
``App.jsx`` never calls the execution engine, so editing can neither erase
nor auto-relaunch executions.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONV_JS = ROOT / "frontend" / "src" / "lib" / "conversations.js"
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"


@unittest.skipIf(shutil.which("node") is None, "node is required to drive the real conversations.js")
class ConversationEditHarnessTest(unittest.TestCase):
    """Runs the REAL conversations.js under node.

    Only the ``./plans.js`` import is stubbed (``uid``), because its module
    graph pulls JSX that node cannot parse. Every function under test is the
    byte-identical project code.
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

const user = (id, text, extra = {}) => ({ id, role: "user", kind: "text", text, entryId: null, ...extra });
const reply = (id, kind, extra = {}) => ({ id, role: "assistant", kind, text: kind === "text" ? "Hi" : "", entryId: null, ...extra });
const thread = () => [
  user("u1", "Plan my trip"),
  reply("a1", "plan", { entryId: "plan-1" }),
  user("u2", "And hotels?"),
  reply("a2", "text"),
  user("u3", "Thanks"),
  reply("a3", "text"),
];

// 1. Edit the FIRST message: text replaced, previous kept, reply designated.
{
  const edit = applyTextEdit(thread(), "u1", "Plan my beach trip");
  assert.ok(edit);
  assert.equal(edit.userIndex, 0);
  assert.equal(edit.replyIndex, 1);
  assert.equal(edit.replyKind, "plan");
  assert.equal(edit.messages[0].text, "Plan my beach trip");
  assert.equal(edit.messages[0].edited, true);
  assert.equal(edit.messages[0].previousText, "Plan my trip");
  assert.equal(edit.messages[0].id, "u1");
  // Later messages untouched (same ids and contents).
  assert.deepEqual(edit.messages.slice(1).map((m) => m.id), ["a1", "u2", "a2", "u3", "a3"]);
  assert.equal(edit.messages[2].text, "And hotels?");
}
// 2. Edit a MIDDLE message.
{
  const edit = applyTextEdit(thread(), "u2", "And hostels?");
  assert.ok(edit);
  assert.equal(edit.userIndex, 2);
  assert.equal(edit.replyIndex, 3);
  assert.equal(edit.replyKind, "text");
  assert.equal(edit.messages[4].id, "u3");
  assert.equal(edit.messages[5].id, "a3");
  // Regeneration context excludes later messages.
  const before = historyBefore(thread(), edit.userIndex);
  assert.deepEqual(before.map((m) => m.id), ["u1", "a1"]);
}
// 3. Edit the LAST user message with no reply yet.
{
  const messages = [user("u1", "Hello"), reply("a1", "text"), user("u2", "Are you there?")];
  const edit = applyTextEdit(messages, "u2", "Are you still there?");
  assert.ok(edit);
  assert.equal(edit.replyIndex, -1);
  assert.equal(edit.replyKind, null);
  const next = replaceReplyAt(edit.messages, edit, { kind: "text", text: "Yes", entryId: null }, "fresh-id");
  assert.equal(next.length, 4);
  assert.equal(next[3].id, "fresh-id");
  assert.equal(next[3].text, "Yes");
  assert.equal(next[2].text, "Are you still there?");
}
// 4. Invalid edits are refused (cancel path keeps the thread intact).
{
  const messages = thread();
  assert.equal(applyTextEdit(messages, "a1", "nope"), null);
  assert.equal(applyTextEdit(messages, "unknown", "nope"), null);
  assert.equal(applyTextEdit(messages, "u1", "   "), null);
  assert.equal(applyTextEdit(messages, "u1", ""), null);
  assert.equal(messages[0].text, "Plan my trip");
  assert.equal(messages[0].edited, undefined);
}
// 5. Reply replacement keeps the reply id and later messages.
{
  const edit = applyTextEdit(thread(), "u2", "And hostels?");
  const next = replaceReplyAt(edit.messages, edit, { kind: "text", text: "New answer", entryId: null }, "unused");
  assert.equal(next.length, 6);
  assert.equal(next[3].id, "a2");
  assert.equal(next[3].text, "New answer");
  assert.deepEqual(next.slice(4).map((m) => m.id), ["u3", "a3"]);
}
// 6. Reply kinds after a user message (regeneration dispatch).
{
  assert.equal(replyKindAfter([user("u", "x"), reply("a", "image")], 0), "image");
  assert.equal(replyKindAfter([user("u", "x"), reply("a", "segment")], 0), "segment");
  assert.equal(replyKindAfter([user("u", "x"), reply("a", "error")], 0), "error");
  assert.equal(replyKindAfter([user("u", "x"), reply("a", "plan")], 0), "plan");
  assert.equal(replyKindAfter([user("u", "x"), reply("a", "text")], 0), "text");
  assert.equal(replyKindAfter([user("u", "x")], 0), null);
  assert.equal(replyKindAfter([user("u", "x"), user("u2", "y")], 0), null);
}
// 7. Media references persist across save/load; ephemeral data does not.
{
  const MEDIA = "f".repeat(64) + ".png";
  const stored = [
    {
      id: "c1",
      title: "t",
      mode: "chat",
      messages: [
        user("u1", "Draw a fox"),
        {
          ...reply("a1", "image"),
          imagePrompt: "Draw a fox",
          imagePreview: "blob:http://localhost/abc",
          imageMime: "image/png",
          mediaId: MEDIA,
          mediaMime: "image/png",
        },
      ],
      createdAt: 1,
      updatedAt: 2,
    },
  ];
  saveConversations(stripImageData(stored));
  const raw = globalThis.localStorage.getItem("reflow:conversations:v1");
  assert.ok(raw);
  assert.ok(!raw.includes("blob:http"), "blob: URL must never persist");
  assert.ok(raw.includes(MEDIA), "stable media reference must persist");
  const [reloaded] = loadConversations();
  assert.equal(reloaded.messages[1].mediaId, MEDIA);
  assert.equal(reloaded.messages[1].mediaMime, "image/png");
  assert.equal(reloaded.messages[1].imagePreview, undefined);
  assert.equal(reloaded.messages[1].imagePrompt, "Draw a fox");
}
// 8. Edit markers and previous text persist; old data stays compatible.
{
  const stored = [
    {
      id: "c1",
      title: "t",
      mode: "chat",
      messages: [
        { ...user("u1", "New text"), edited: true, previousText: "Old text" },
        reply("a1", "text"),
        user("u2", "Plain"),
      ],
      createdAt: 1,
      updatedAt: 2,
    },
  ];
  saveConversations(stored);
  const [reloaded] = loadConversations();
  assert.equal(reloaded.messages[0].edited, true);
  assert.equal(reloaded.messages[0].previousText, "Old text");
  assert.equal(reloaded.messages[2].edited, false);
  assert.equal(reloaded.messages[2].previousText, "");
  assert.equal(reloaded.messages[2].mediaId, null);
}

console.log("CONVERSATION_EDIT_OK");
"""

    UID_STUB = (
        "let __uidCounter = 0;\n"
        "function uid() {\n"
        '  __uidCounter += 1;\n'
        "  return `test-uid-${__uidCounter}`;\n"
        "}\n"
    )

    def test_conversation_edit_helpers(self):
        source = CONV_JS.read_text(encoding="utf-8")
        import_line = 'import { uid } from "./plans.js";'
        self.assertIn(import_line, source, "conversations.js import contract changed")
        self.assertEqual(source.count("plans.js"), 1, "unexpected new imports in conversations.js")
        harness = source.replace(import_line, self.UID_STUB) + self.HARNESS_TAIL
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "conversation_edit.mjs"
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
            f"conversation edit checks failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )
        self.assertIn("CONVERSATION_EDIT_OK", completed.stdout)


class NoAutoExecutionInvariantTest(unittest.TestCase):
    """Editing a message must never erase or auto-relaunch executions.

    The resend flow regenerates assistant replies through the conversation
    APIs only (chat/plan/image/edit/segment). This pins that the resend code
    in App.jsx references no execution-engine primitive.
    """

    FORBIDDEN = (
        "executeTask(",
        "executeExecTask(",
        "approveTaskState(",
        "startExecutionState(",
        "completeExecutionState(",
        "failExecutionState(",
        "revokeTaskState(",
        "rejectTaskState(",
    )

    def test_resend_flow_calls_no_execution_primitive(self):
        source = APP_JSX.read_text(encoding="utf-8")
        start = source.find("async function produceRegeneratedReply")
        end = source.find("function setConversationMode")
        self.assertGreater(start, 0, "resend flow not found in App.jsx")
        self.assertGreater(end, start, "resend flow boundaries changed in App.jsx")
        resend_code = source[start:end]
        self.assertIn("resendEditedMessage", resend_code)
        for token in self.FORBIDDEN:
            self.assertNotIn(token, resend_code, f"resend flow must not call {token}")

    def test_execution_store_untouched_by_conversation_files(self):
        conv_source = CONV_JS.read_text(encoding="utf-8")
        for token in ("reflow:execution", "execute", "approval"):
            self.assertNotIn(token, conv_source)


if __name__ == "__main__":
    unittest.main()
