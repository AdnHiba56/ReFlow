// Document Intelligence workspace (browser side).
// The backend (reflow-docs/) owns extracted text; the browser keeps document
// metadata, per-document question threads and analysis results only — never
// the full text, never file bytes. Threads and results are keyed by doc_id
// so two documents are never mixed.
export const DOC_STORAGE_KEY = "reflow:documents:v1";
export const DOC_OPERATIONS = [
  "summarize",
  "extract",
  "simplify",
  "explain",
  "ask",
  "action_items",
];
const MAX_DOCS = 10;
const MAX_THREAD_ENTRIES = 50;

export function emptyDocStore() {
  return { version: 1, docs: [], currentId: null, threads: {}, results: {} };
}

function asRecordMap(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeDoc(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.doc_id !== "string") return null;
  return {
    doc_id: raw.doc_id,
    filename: typeof raw.filename === "string" ? raw.filename : "document",
    format: typeof raw.format === "string" ? raw.format : "",
    pages: typeof raw.pages === "number" ? raw.pages : 0,
    chars: typeof raw.chars === "number" ? raw.chars : 0,
    status: raw.status === "needs_ocr" ? "needs_ocr" : "ready",
    preview: typeof raw.preview === "string" ? raw.preview.slice(0, 2000) : "",
    updatedAt: typeof raw.updatedAt === "number" ? raw.updatedAt : 0,
  };
}

function normalizeThread(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((entry) => entry && typeof entry === "object" && typeof entry.q === "string")
    .map((entry) => ({
      q: entry.q,
      a: typeof entry.a === "string" ? entry.a : "",
      refs: Array.isArray(entry.refs) ? entry.refs.filter((ref) => typeof ref === "string") : [],
      ts: typeof entry.ts === "number" ? entry.ts : 0,
    }))
    .slice(-MAX_THREAD_ENTRIES);
}

function normalizeResults(raw) {
  const clean = {};
  for (const [operation, payload] of Object.entries(asRecordMap(raw))) {
    if (!DOC_OPERATIONS.includes(operation) || !payload || typeof payload !== "object") continue;
    clean[operation] = {
      result: typeof payload.result === "string" ? payload.result : "",
      refs: Array.isArray(payload.refs) ? payload.refs.filter((ref) => typeof ref === "string") : [],
      language: payload.language === "fr" ? "fr" : "en",
      found: payload.found !== false,
      message: typeof payload.message === "string" ? payload.message : "",
      ts: typeof payload.ts === "number" ? payload.ts : 0,
    };
  }
  return clean;
}

export function loadDocStore() {
  try {
    const raw = localStorage.getItem(DOC_STORAGE_KEY);
    if (!raw) return emptyDocStore();
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return emptyDocStore();
    const docs = Array.isArray(data.docs)
      ? data.docs.map(normalizeDoc).filter(Boolean).slice(0, MAX_DOCS)
      : [];
    const ids = new Set(docs.map((doc) => doc.doc_id));
    const threads = {};
    for (const [docId, thread] of Object.entries(asRecordMap(data.threads))) {
      if (ids.has(docId)) threads[docId] = normalizeThread(thread);
    }
    const results = {};
    for (const [docId, payload] of Object.entries(asRecordMap(data.results))) {
      if (ids.has(docId)) results[docId] = normalizeResults(payload);
    }
    const currentId =
      typeof data.currentId === "string" && ids.has(data.currentId) ? data.currentId : null;
    return { version: 1, docs, currentId, threads, results };
  } catch {
    return emptyDocStore();
  }
}

export function saveDocStore(store) {
  try {
    const docs = (store?.docs || []).filter((doc) => doc && typeof doc.doc_id === "string").slice(0, MAX_DOCS);
    const ids = new Set(docs.map((doc) => doc.doc_id));
    const threads = {};
    for (const [docId, thread] of Object.entries(asRecordMap(store?.threads))) {
      if (ids.has(docId)) threads[docId] = normalizeThread(thread);
    }
    const results = {};
    for (const [docId, payload] of Object.entries(asRecordMap(store?.results))) {
      if (ids.has(docId)) results[docId] = normalizeResults(payload);
    }
    localStorage.setItem(
      DOC_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        docs,
        currentId: typeof store?.currentId === "string" && ids.has(store.currentId) ? store.currentId : null,
        threads,
        results,
      })
    );
  } catch {
    // Quota or privacy mode: the app keeps working for this session.
  }
}

export function currentDoc(store) {
  return (store?.docs || []).find((doc) => doc.doc_id === store?.currentId) || null;
}

export function currentThread(store) {
  if (!store?.currentId) return [];
  return store?.threads?.[store.currentId] || [];
}

export function currentResult(store, operation) {
  if (!store?.currentId) return null;
  return store?.results?.[store.currentId]?.[operation] || null;
}

// Adds or refreshes a session document (dedup by doc_id, most recent first).
export function rememberDoc(store, info) {
  const doc = normalizeDoc({ ...info, updatedAt: Date.now() });
  if (!doc) return store;
  const docs = [doc, ...(store?.docs || []).filter((item) => item.doc_id !== doc.doc_id)].slice(0, MAX_DOCS);
  const ids = new Set(docs.map((item) => item.doc_id));
  const threads = {};
  for (const [docId, thread] of Object.entries(asRecordMap(store?.threads))) {
    if (ids.has(docId)) threads[docId] = thread;
  }
  const results = {};
  for (const [docId, payload] of Object.entries(asRecordMap(store?.results))) {
    if (ids.has(docId)) results[docId] = payload;
  }
  return { ...store, docs, currentId: doc.doc_id, threads, results };
}

// Forgets a session document (client-side only; backend files are kept).
export function forgetDoc(store, docId) {
  const docs = (store?.docs || []).filter((doc) => doc.doc_id !== docId);
  const threads = { ...asRecordMap(store?.threads) };
  const results = { ...asRecordMap(store?.results) };
  delete threads[docId];
  delete results[docId];
  const currentId = store?.currentId === docId ? (docs[0]?.doc_id || null) : store?.currentId || null;
  return { ...store, docs, currentId, threads, results };
}

export function selectDoc(store, docId) {
  if (!(store?.docs || []).some((doc) => doc.doc_id === docId)) return store;
  return { ...store, currentId: docId };
}

export function pushQA(store, docId, entry) {
  const thread = normalizeThread([...((store?.threads || {})[docId] || []), entry]);
  return { ...store, threads: { ...asRecordMap(store?.threads), [docId]: thread } };
}

export function putResult(store, docId, operation, payload) {
  if (!DOC_OPERATIONS.includes(operation)) return store;
  const scoped = normalizeResults({ [operation]: payload });
  const previous = asRecordMap((store?.results || {})[docId]);
  return {
    ...store,
    results: { ...asRecordMap(store?.results), [docId]: { ...previous, ...scoped } },
  };
}
