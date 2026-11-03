import { useState } from "react";
import {
  EXEC_ACTIONS,
  approvalState,
  buildActionParams,
  defaultDirname,
  defaultFilename,
  execKey,
  getExecution,
  readiness,
} from "../lib/execution.js";

function uiParamsFromSnapshot(actionType, snapshotParams, task) {
  const params = snapshotParams && typeof snapshotParams === "object" ? snapshotParams : {};
  if (actionType === "document.create" || actionType === "plan.organize") {
    return {
      filename:
        typeof params.filename === "string" && params.filename
          ? params.filename
          : defaultFilename(task?.id),
      confirm_overwrite: params.confirm_overwrite === true,
    };
  }
  if (actionType === "file.organize") {
    const first = Array.isArray(params.operations) ? params.operations[0] : null;
    return {
      dirname:
        first && typeof first.name === "string" && first.name
          ? first.name
          : defaultDirname(task?.id),
    };
  }
  if (actionType === "email.draft") {
    return {
      to: typeof params.to === "string" ? params.to : "",
      subject: typeof params.subject === "string" ? params.subject : "",
    };
  }
  return {};
}

const APPROVAL_LABELS = {
  approved: "Approved",
  stale: "Approval outdated",
  rejected: "Rejected",
  none: "Not approved",
};

function ExecutionResult({ record }) {
  const result = record?.result && typeof record.result === "object" ? record.result : {};
  return (
    <div className="exec-result">
      <p>{record.result_summary}</p>
      {typeof result.filename === "string" && (
        <p className="muted">
          {result.filename}
          {typeof result.bytes === "number" ? ` · ${result.bytes} bytes` : ""}
        </p>
      )}
      {typeof result.preview === "string" && result.preview && (
        <pre className="exec-preview">{result.preview}</pre>
      )}
      {result.brief && typeof result.brief === "object" && (
        <div className="exec-brief">
          <p className="muted">Provided facts (unverified):</p>
          <ul>
            {(result.brief.provided_facts || []).map((fact, index) => (
              <li key={index}>{fact}</li>
            ))}
          </ul>
          <p className="muted">Open questions:</p>
          <ul>
            {(result.brief.open_questions || []).map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
          {typeof result.brief.verification === "string" && (
            <p className="muted">{result.brief.verification}</p>
          )}
        </div>
      )}
      {typeof result.body === "string" && result.body && (
        <>
          {typeof result.subject === "string" && result.subject && (
            <p className="muted">Subject: {result.subject}</p>
          )}
          <pre className="exec-preview">{result.body}</pre>
          {typeof result.note === "string" && <p className="muted">{result.note}</p>}
        </>
      )}
      {Array.isArray(result.operations) && (
        <ul>
          {result.operations.map((op, index) => (
            <li key={index}>
              {op.op}: {op.path}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Approval + execution controls for one structured-plan task.
// Display only: all state transitions run through the callbacks in App.
export default function TaskExecutionPanel({ planId, task, entry, execution }) {
  const { store, running, actions, approve, reject, revoke, execute, cancel } = execution;
  // Backend specs carry {type, description, side_effects, safe_retry}: enrich them
  // with the local label/fields so param inputs always render.
  const localByType = Object.fromEntries(EXEC_ACTIONS.map((item) => [item.type, item]));
  const rawSpecs = Array.isArray(actions) && actions.length > 0 ? actions : EXEC_ACTIONS;
  const specs = rawSpecs.map((item) => ({
    ...localByType[item.type],
    ...item,
    fields: localByType[item.type]?.fields || [],
  }));
  const storedApproval = store?.approvals?.[execKey(planId, task.id)] || null;
  const initialAction =
    storedApproval?.snapshot?.action?.type &&
    specs.some((spec) => spec.type === storedApproval.snapshot.action.type)
      ? storedApproval.snapshot.action.type
      : specs[0].type;
  const [actionType, setActionType] = useState(initialAction);
  const [uiParams, setUiParams] = useState(() =>
    uiParamsFromSnapshot(initialAction, storedApproval?.snapshot?.action?.params, task)
  );
  const apiParams = buildActionParams(actionType, uiParams, task);
  const approval = approvalState(store, planId, task, actionType, apiParams);
  const record = getExecution(store, planId, task.id);
  const ready = readiness(store, entry, task, actionType, apiParams, { running });
  const runningThis = record?.status === "running";
  const spec = specs.find((item) => item.type === actionType) || {};
  const fields = Array.isArray(spec.fields) ? spec.fields : [];

  function setField(name, value) {
    setUiParams((prev) => ({ ...prev, [name]: value }));
  }

  function pickAction(next) {
    setActionType(next);
    setUiParams(uiParamsFromSnapshot(next, null, task));
  }

  return (
    <div className="exec-panel">
      <div className="exec-row">
        <span className={`status-badge approval-${approval.state}`}>
          {APPROVAL_LABELS[approval.state] || approval.state}
        </span>
        {approval.state === "none" && (
          <>
            <button type="button" className="primary small" onClick={() => approve(planId, task, actionType, uiParams)}>
              Approve
            </button>
            <button type="button" className="secondary small" onClick={() => reject(planId, task)}>
              Reject
            </button>
          </>
        )}
        {approval.state === "approved" && (
          <button type="button" className="secondary small" onClick={() => revoke(planId, task)}>
            Revoke approval
          </button>
        )}
        {approval.state === "stale" && (
          <>
            <button type="button" className="primary small" onClick={() => approve(planId, task, actionType, uiParams)}>
              Approve again
            </button>
            <button type="button" className="secondary small" onClick={() => revoke(planId, task)}>
              Revoke
            </button>
          </>
        )}
        {approval.state === "rejected" && (
          <button type="button" className="secondary small" onClick={() => approve(planId, task, actionType, uiParams)}>
            Approve anyway
          </button>
        )}
      </div>

      <div className="exec-row">
        <label>
          Action{" "}
          <select value={actionType} onChange={(event) => pickAction(event.target.value)} disabled={runningThis}>
            {specs.map((item) => (
              <option key={item.type} value={item.type}>
                {item.label || item.type}
              </option>
            ))}
          </select>
        </label>
        {fields.includes("filename") && (
          <label>
            Filename{" "}
            <input
              type="text"
              value={uiParams.filename || ""}
              onChange={(event) => setField("filename", event.target.value)}
              disabled={runningThis}
              autoComplete="off"
            />
          </label>
        )}
        {fields.includes("dirname") && (
          <label>
            New directory{" "}
            <input
              type="text"
              value={uiParams.dirname || ""}
              onChange={(event) => setField("dirname", event.target.value)}
              disabled={runningThis}
              autoComplete="off"
            />
          </label>
        )}
        {fields.includes("to") && (
          <label>
            To{" "}
            <input
              type="text"
              value={uiParams.to || ""}
              onChange={(event) => setField("to", event.target.value)}
              disabled={runningThis}
              autoComplete="off"
              placeholder="recipient (draft only)"
            />
          </label>
        )}
        {fields.includes("subject") && (
          <label>
            Subject{" "}
            <input
              type="text"
              value={uiParams.subject || ""}
              onChange={(event) => setField("subject", event.target.value)}
              disabled={runningThis}
              autoComplete="off"
            />
          </label>
        )}
      </div>

      {fields.includes("filename") && (
        <label className="exec-check">
          <input
            type="checkbox"
            checked={uiParams.confirm_overwrite === true}
            onChange={(event) => setField("confirm_overwrite", event.target.checked)}
            disabled={runningThis}
          />{" "}
          Allow overwriting an existing file
        </label>
      )}

      <div className="exec-row">
        <button
          type="button"
          className="primary small"
          disabled={!ready.ok || runningThis}
          title={ready.ok ? "Execute this approved task" : ready.reasons.join(" ")}
          onClick={() => execute(entry, task, actionType, uiParams)}
        >
          Execute
        </button>
        {runningThis && (
          <button type="button" className="secondary small" onClick={() => cancel()}>
            Cancel
          </button>
        )}
      </div>
      {!ready.ok && !runningThis && <p className="muted">{ready.reasons[0]}</p>}
      {runningThis && (
        <p className="muted" aria-live="polite">
          Executing…
        </p>
      )}
      {record?.status === "completed" && <ExecutionResult record={record} />}
      {(record?.status === "failed" || record?.status === "interrupted") && (
        <p className="exec-error" role="alert">
          {record.error || "Execution failed."}
        </p>
      )}
      {record?.status === "cancelled" && <p className="muted">Cancelled.</p>}
    </div>
  );
}
