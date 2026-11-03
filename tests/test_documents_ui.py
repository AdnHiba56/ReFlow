"""Document Intelligence store (real frontend/src/lib/documents.js via node).

The workspace module has no imports, so node runs it directly with only a
localStorage stub. Covered: doc registry with dedup and bounds, per-doc
threads and results that never mix, session removal, and save/load
round-trips under the existing key.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_JS = ROOT / "frontend" / "src" / "lib" / "documents.js"


@unittest.skipIf(shutil.which("node") is None, "node is required to drive the real documents.js")
class DocStoreHarnessTest(unittest.TestCase):
    HARNESS_TAIL = r"""
import assert from "node:assert";

globalThis.localStorage = {
  _data: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._data, k) ? this._data[k] : null; },
  setItem(k, v) { this._data[k] = String(v); },
  removeItem(k) { delete this._data[k]; },
  clear() { this._data = {}; },
};

const info = (id, name = "a.pdf") => ({
  doc_id: id,
  filename: name,
  format: "pdf",
  pages: 2,
  chars: 100,
  status: "ready",
  preview: "hello",
});

// 1. Registry: upsert with dedup, most recent first, current follows.
{
  let store = emptyDocStore();
  store = rememberDoc(store, info("a".repeat(64), "a.pdf"));
  store = rememberDoc(store, info("b".repeat(64), "b.pdf"));
  assert.equal(store.docs.length, 2);
  assert.equal(store.currentId, "b".repeat(64));
  assert.equal(currentDoc(store).filename, "b.pdf");
  store = rememberDoc(store, info("a".repeat(64), "a-renamed.pdf"));
  assert.equal(store.docs.length, 2);
  assert.equal(store.docs[0].filename, "a-renamed.pdf");
  assert.equal(store.currentId, "a".repeat(64));
}
// 2. Registry bounded; evicted docs lose threads and results.
{
  let store = emptyDocStore();
  for (let i = 0; i < 12; i += 1) {
    const id = String(i).padStart(64, "0");
    store = rememberDoc(store, info(id, `d${i}.pdf`));
    store = pushQA(store, id, { q: "q", a: "a", refs: [], ts: 1 });
  }
  assert.equal(store.docs.length, 10);
  assert.ok(!store.docs.some((doc) => doc.filename === "d0.pdf"));
  assert.equal(store.threads["0".repeat(64)], undefined);
}
// 3. Threads and results never mix between documents.
{
  let store = emptyDocStore();
  const ida = "a".repeat(64);
  const idb = "b".repeat(64);
  store = rememberDoc(store, info(ida, "a.pdf"));
  store = rememberDoc(store, info(idb, "b.pdf"));
  store = pushQA(store, ida, { q: "About A?", a: "Answer A", refs: ["Page 1"], ts: 1 });
  store = putResult(store, idb, "summarize", { result: "Summary B", refs: [], language: "en", found: true, message: "", ts: 2 });
  assert.deepEqual(currentThread(store), []);
  store = selectDoc(store, ida);
  assert.equal(currentThread(store).length, 1);
  assert.equal(currentThread(store)[0].a, "Answer A");
  assert.equal(currentResult(store, "summarize"), null);
  store = selectDoc(store, idb);
  assert.equal(currentResult(store, "summarize").result, "Summary B");
  assert.deepEqual(currentThread(store), []);
  assert.equal(selectDoc(store, "unknown"), store);
  assert.equal(putResult(store, idb, "nope", {}), store);
}
// 4. Session removal forgets doc, thread and results; current falls back.
{
  let store = emptyDocStore();
  const ida = "a".repeat(64);
  const idb = "b".repeat(64);
  store = rememberDoc(store, info(ida, "a.pdf"));
  store = rememberDoc(store, info(idb, "b.pdf"));
  store = pushQA(store, idb, { q: "q", a: "a", refs: [], ts: 1 });
  store = forgetDoc(store, idb);
  assert.equal(store.docs.length, 1);
  assert.equal(store.currentId, ida);
  assert.equal(store.threads[idb], undefined);
  store = forgetDoc(store, ida);
  assert.equal(store.docs.length, 0);
  assert.equal(store.currentId, null);
}
// 5. Save/load round-trip under the existing key; corrupt data resets.
{
  let store = emptyDocStore();
  const ida = "a".repeat(64);
  store = rememberDoc(store, info(ida, "a.pdf"));
  store = pushQA(store, ida, { q: "q?", a: "a!", refs: ["Page 2"], ts: 7 });
  store = putResult(store, ida, "extract", { result: "items", refs: ["Page 2"], language: "fr", found: true, message: "", ts: 8 });
  saveDocStore(store);
  assert.equal(DOC_STORAGE_KEY, "reflow:documents:v1");
  const raw = globalThis.localStorage.getItem("reflow:documents:v1");
  assert.ok(raw && raw.includes(ida));
  const reloaded = loadDocStore();
  assert.equal(reloaded.currentId, ida);
  assert.equal(reloaded.threads[ida][0].a, "a!");
  assert.equal(reloaded.results[ida].extract.language, "fr");
  globalThis.localStorage.setItem("reflow:documents:v1", "not json{{{");
  assert.deepEqual(loadDocStore(), emptyDocStore());
  globalThis.localStorage.setItem("reflow:documents:v1", JSON.stringify({ docs: [{ doc_id: 42 }], currentId: "x" }));
  const cleaned = loadDocStore();
  assert.equal(cleaned.docs.length, 0);
  assert.equal(cleaned.currentId, null);
}

console.log("DOCSTORE_OK");
"""

    def test_doc_store(self):
        source = DOCS_JS.read_text(encoding="utf-8")
        self.assertNotIn(" from ", source, "documents.js must stay import-free for the harness")
        harness = source + self.HARNESS_TAIL
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "docstore.mjs"
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
            f"doc store checks failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )
        self.assertIn("DOCSTORE_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
