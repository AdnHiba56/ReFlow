import { useState } from "react";
import { currentDoc, currentResult, currentThread } from "../lib/documents.js";

const ACCEPT = ".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
const MAX_BYTES = 10 * 1024 * 1024;
const EXTENSIONS = { ".pdf": "pdf", ".docx": "docx", ".txt": "txt" };
const MIME_FOR_EXT = {
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".txt": "text/plain",
};

const OPERATIONS = [
  { id: "summarize", label: "Summarize" },
  { id: "extract", label: "Extract Key Information" },
  { id: "simplify", label: "Simplify" },
  { id: "explain", label: "Explain" },
  { id: "action_items", label: "Action Items" },
];

const STYLES = [
  { id: "simple", label: "Simple language" },
  { id: "professional", label: "Professional" },
  { id: "academic", label: "Academic" },
  { id: "short", label: "Short version" },
  { id: "detailed", label: "Detailed version" },
];

function extOf(filename) {
  const dot = filename.toLowerCase().lastIndexOf(".");
  return dot >= 0 ? filename.toLowerCase().slice(dot) : "";
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      if (typeof dataUrl !== "string" || !dataUrl.includes(",")) {
        reject(new Error("Could not read the file."));
        return;
      }
      resolve(dataUrl.slice(dataUrl.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error("Could not read the file."));
    reader.readAsDataURL(file);
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Refs({ refs }) {
  if (!refs || refs.length === 0) return null;
  return (
    <p className="task-deps">
      Sources:{" "}
      {refs.map((ref, index) => (
        <span key={`${ref}-${index}`}>
          {index > 0 && ", "}
          <span className="dep-chip">{ref}</span>
        </span>
      ))}
    </p>
  );
}

// Document Intelligence workspace: upload, analysis operations, per-document
// Q&A thread and plan creation. Display only: state lives in App.
export default function Documents({ documents }) {
  const { store, busy } = documents;
  const doc = currentDoc(store);
  const thread = currentThread(store);
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [activeOp, setActiveOp] = useState("summarize");
  const [style, setStyle] = useState("simple");
  const [language, setLanguage] = useState("en");
  const [opError, setOpError] = useState("");
  const [question, setQuestion] = useState("");
  const [qaError, setQaError] = useState("");
  const [planError, setPlanError] = useState("");
  const [copied, setCopied] = useState(false);

  const working = busy !== null;
  const analyzing = busy?.kind === "analyze";
  const result = currentResult(store, activeOp);

  function pickFile(event) {
    const selected = event.target.files && event.target.files[0];
    event.target.value = "";
    setFileError("");
    setUploadError("");
    if (!selected) return;
    const ext = extOf(selected.name || "");
    if (!EXTENSIONS[ext]) {
      setFileError("Unsupported file type: only PDF, DOCX or TXT.");
      return;
    }
    if (selected.size > MAX_BYTES || selected.size <= 0) {
      setFileError("File too large or empty: 10 MB maximum.");
      return;
    }
    setFile(selected);
  }

  async function submitUpload(event) {
    event.preventDefault();
    if (!file || working) return;
    setUploadError("");
    try {
      const data = await readAsBase64(file);
      await documents.upload({
        filename: file.name,
        mime: MIME_FOR_EXT[extOf(file.name)],
        data,
      });
      setFile(null);
      setActiveOp("summarize");
      setOpError("");
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed: try again.");
    }
  }

  async function runOperation(operation) {
    if (!doc || working) return;
    setActiveOp(operation);
    setOpError("");
    setCopied(false);
    try {
      await documents.analyze(operation, { style, language });
    } catch (err) {
      setOpError(err instanceof Error ? err.message : "Analysis failed: try again.");
    }
  }

  async function submitQuestion(event) {
    event.preventDefault();
    const text = question.trim();
    if (!doc || !text || working) return;
    setQaError("");
    try {
      await documents.ask(text, { language });
      setQuestion("");
    } catch (err) {
      setQaError(err instanceof Error ? err.message : "Question failed: try again.");
    }
  }

  async function submitPlan() {
    if (!doc || working) return;
    setPlanError("");
    try {
      await documents.createPlan();
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "Could not create a plan.");
    }
  }

  async function copyResult() {
    if (!result?.result) return;
    setCopied(await copyText(result.result));
  }

  return (
    <div className="view">
      <header className="view-head">
        <h2>Documents</h2>
        <p className="muted">
          Import a PDF, DOCX or TXT file: ReFlow reads it, then summarizes, explains
          and answers questions about it. Nothing runs automatically.
        </p>
      </header>

      <section className="card" aria-label="Import a document">
        <form className="doc-upload" onSubmit={submitUpload}>
          <label className="field-label" htmlFor="doc-file">
            Import a document
          </label>
          <input
            id="doc-file"
            type="file"
            accept={ACCEPT}
            onChange={pickFile}
            disabled={working}
          />
          <p className="muted">PDF, DOCX or TXT · 10 MB maximum · checked by format and content.</p>
          {file && (
            <p>
              Selected: <strong>{file.name}</strong> · {formatBytes(file.size)}
            </p>
          )}
          {fileError && (
            <p className="voice-error" role="alert">
              {fileError}
            </p>
          )}
          <div className="composer-row">
            <span />
            <span className="composer-buttons">
              <button type="submit" className="primary" disabled={!file || working}>
                {busy?.kind === "upload" ? "Importing…" : "Import"}
              </button>
            </span>
          </div>
          {uploadError && (
            <p className="voice-error" role="alert">
              {uploadError}
            </p>
          )}
        </form>
      </section>

      {store.docs.length === 0 ? (
        <section className="card">
          <div className="empty">
            <p className="empty-title">No document imported</p>
            <p className="muted">Import a file above to start reading and analyzing it.</p>
          </div>
        </section>
      ) : (
        <div className="plans-layout">
          <section className="card plans-list" aria-label="Imported documents">
            <ul>
              {store.docs.map((item) => (
                <li key={item.doc_id} className="doc-item">
                  <button
                    type="button"
                    className={doc && item.doc_id === doc.doc_id ? "plan-item active" : "plan-item"}
                    aria-current={doc && item.doc_id === doc.doc_id ? "true" : undefined}
                    onClick={() => documents.select(item.doc_id)}
                  >
                    <span className="plan-item-title">{item.filename}</span>
                    <span className="plan-item-meta">
                      {item.format.toUpperCase()} ·{" "}
                      {item.format === "pdf" ? `${item.pages} pages · ` : ""}
                      {item.chars} chars{item.status === "needs_ocr" ? " · scanned" : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="secondary small"
                    onClick={() => documents.remove(item.doc_id)}
                    disabled={working}
                    aria-label={`Remove ${item.filename} from this session`}
                    title="Remove from this session (stored analysis stays on the server)"
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </section>

          {doc && (
            <div className="plans-detail">
              <section className="card" aria-label="Document overview">
                <h3>{doc.filename}</h3>
                <p className="muted">
                  {doc.format.toUpperCase()}
                  {doc.format === "pdf" ? ` · ${doc.pages} pages` : ""} · {doc.chars} characters
                </p>
                {doc.status === "needs_ocr" ? (
                  <div className="missing-banner" role="note">
                    <h4>Scanned document</h4>
                    <p>
                      No extractable text was found in this PDF (probably scanned images).
                      OCR is not available, so text analysis is disabled for it.
                    </p>
                  </div>
                ) : (
                  doc.preview && <pre className="exec-preview doc-preview">{doc.preview}</pre>
                )}
              </section>

              <section className="card" aria-label="Analysis operations">
                <h3>Analyze</h3>
                <div className="exec-row">
                  <label>
                    Language{" "}
                    <select value={language} onChange={(event) => setLanguage(event.target.value)} disabled={working}>
                      <option value="en">English</option>
                      <option value="fr">Français</option>
                    </select>
                  </label>
                  <label>
                    Simplify style{" "}
                    <select value={style} onChange={(event) => setStyle(event.target.value)} disabled={working}>
                      {STYLES.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="doc-ops" role="group" aria-label="Operations">
                  {OPERATIONS.map((operation) => (
                    <button
                      key={operation.id}
                      type="button"
                      className={activeOp === operation.id ? "secondary small active" : "secondary small"}
                      disabled={working || doc.status === "needs_ocr"}
                      onClick={() => runOperation(operation.id)}
                    >
                      {operation.label}
                    </button>
                  ))}
                </div>
                {analyzing && (
                  <p className="muted" aria-live="polite">
                    Analyzing with Muse Spark…
                  </p>
                )}
                {opError && (
                  <p className="voice-error" role="alert">
                    {opError}
                  </p>
                )}
                {result && result.result && (
                  <div className="doc-result">
                    <pre className="exec-preview">{result.result}</pre>
                    <Refs refs={result.refs} />
                    {result.message && <p className="muted">{result.message}</p>}
                    <div className="composer-row">
                      <span />
                      <span className="composer-buttons">
                        <button type="button" className="secondary small" onClick={copyResult}>
                          {copied ? "Copied" : "Copy result"}
                        </button>
                      </span>
                    </div>
                  </div>
                )}
              </section>

              <section className="card" aria-label="Ask questions about the document">
                <h3>Ask questions</h3>
                {thread.length > 0 && (
                  <div className="doc-thread">
                    {thread.map((entry, index) => (
                      <div key={index}>
                        <div className="message user">
                          <div className="bubble user">
                            <p>{entry.q}</p>
                          </div>
                        </div>
                        <div className="message assistant">
                          <div className="bubble assistant">
                            <p>{entry.a}</p>
                            <Refs refs={entry.refs} />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <form className="doc-upload" onSubmit={submitQuestion}>
                  <label className="field-label" htmlFor="doc-question">
                    Your question
                  </label>
                  <textarea
                    id="doc-question"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="E.g. What are the most important conclusions?"
                    rows={2}
                    maxLength={2000}
                    disabled={working || doc.status === "needs_ocr"}
                  />
                  <div className="composer-row">
                    <span className="counter" aria-live="polite">
                      {2000 - question.length} characters left
                    </span>
                    <span className="composer-buttons">
                      <button
                        type="submit"
                        className="primary small"
                        disabled={working || !question.trim() || doc.status === "needs_ocr"}
                      >
                        {analyzing ? "Asking…" : "Ask"}
                      </button>
                    </span>
                  </div>
                </form>
                {qaError && (
                  <p className="voice-error" role="alert">
                    {qaError}
                  </p>
                )}
              </section>

              <section className="card" aria-label="Create an action plan">
                <h3>Action plan</h3>
                <p className="muted">
                  Turn the extracted information into a structured plan. Tasks are proposed
                  for your approval — nothing is executed automatically.
                </p>
                <button
                  type="button"
                  className="primary small"
                  disabled={working || doc.status === "needs_ocr"}
                  onClick={submitPlan}
                >
                  Create structured plan
                </button>
                {planError && (
                  <p className="voice-error" role="alert">
                    {planError}
                  </p>
                )}
              </section>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
