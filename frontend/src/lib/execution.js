// Phase 4 execution state (approvals, executions, history) in localStorage.
// Single-user local demo: the browser is the store. The backend stays
// stateless and re-validates the approval snapshot sent with each request,
// so editing a task invalidates its approval on both sides.
import { effectiveStatus } from "./agentPlans.js";

export const EXEC_STORAGE_KEY = "reflow:execution:v1";
const MAX_HISTORY_EVENTS = 200;
const MAX_KEYED_RECORDS = 100;

// Mirrors the backend action registry (backend stays authoritative).
export const EXEC_ACTIONS = [
  {
    type: "document.create",
    label: "Create document",
    sideEffects: true,
    safeRetry: false,
    fields: ["filename"],
  },
  {
    type: "plan.organize",
    label: "Organize plan",
    sideEffects: true,
    safeRetry: false,
    fields: ["filename"],
  },
  {
    type: "research.summarize",
    label: "Research summary",
    sideEffects: false,
    safeRetry: true,
    fields: [],
  },
  {
    type: "email.draft",
    label: "Email draft",
    sideEffects: false,
    safeRetry: true,
    fields: ["to", "subject"],
  },
  {
    type: "file.organize",
    label: "Organize files",
    sideEffects: true,
    safeRetry: false,
    fields: ["dirname"],
  },
];

export function execKey(planId, taskId) {
  return `${planId}:${taskId}`;
}

export function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  const keys = Object.keys(value)
    .filter((key) => typeof value[key] !== "undefined" && typeof value[key] !== "function")
    .sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

export function snapshotsEqual(a, b) {
  return stableStringify(a) === stableStringify(b);
}

export function defaultFilename(taskId) {
  const clean = String(taskId || "task").replace(/[^a-zA-Z0-9-_]+/g, "-").slice(0, 40) || "task";
  return `task-${clean}.md`;
}

export function defaultDirname(taskId) {
  const clean = String(taskId || "task").replace(/[^a-zA-Z0-9-_]+/g, "-").slice(0, 40) || "task";
  return `task-${clean}`;
}

// UI-level params (form fields) -> API-level params (what the backend runs).
// Approval binds the API-level form, so edits invalidate it on both sides.
export function buildActionParams(actionType, uiParams, task) {
  const ui = uiParams && typeof uiParams === "object" ? uiParams : {};
  if (actionType === "document.create" || actionType === "plan.organize") {
    return {
      filename:
        typeof ui.filename === "string" && ui.filename.trim()
          ? ui.filename.trim()
          : defaultFilename(task?.id),
      ...(typeof ui.confirm_overwrite === "boolean" ? { confirm_overwrite: ui.confirm_overwrite } : {}),
    };
  }
  if (actionType === "file.organize") {
    const dirname =
      typeof ui.dirname === "string" && ui.dirname.trim()
        ? ui.dirname.trim()
        : defaultDirname(task?.id);
    return { operations: [{ op: "mkdir", name: dirname }] };
  }
  if (actionType === "email.draft") {
    return {
      to: typeof ui.to === "string" ? ui.to : "",
      subject: typeof ui.subject === "string" ? ui.subject : "",
      tone: "neutral",
    };
  }
  return {};
}

export function buildSnapshot(task, actionType, apiParams) {
  return {
    task: {
      id: task.id,
      title: task.title,
      detail: task.detail || "",
      depends_on: [...(task.depends_on || [])],
      success_criteria: [...(task.success_criteria || [])],
      needs_info: [...(task.needs_info || [])],
      status: task.status,
    },
    action: { type: actionType, params: apiParams },
  };
}

export function emptyExecutionStore() {
  return { version: 1, approvals: {}, rejections: {}, executions: {}, history: [] };
}

function asRecordMap(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function trimMap(map, stampKey) {
  const entries = Object.entries(map);
  if (entries.length <= MAX_KEYED_RECORDS) return map;
  entries.sort((a, b) => String(b[1]?.[stampKey] || "").localeCompare(String(a[1]?.[stampKey] || "")));
  return Object.fromEntries(entries.slice(0, MAX_KEYED_RECORDS));
}

export function loadExecutionStore() {
  try {
    const raw = localStorage.getItem(EXEC_STORAGE_KEY);
    if (!raw) return emptyExecutionStore();
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return emptyExecutionStore();
    const executions = asRecordMap(data.executions);
    // Crash/reload during a run: a stuck "running" record becomes "interrupted".
    for (const record of Object.values(executions)) {
      if (record && record.status === "running") record.status = "interrupted";
    }
    return {
      version: 1,
      approvals: trimMap(asRecordMap(data.approvals), "approved_at"),
      rejections: trimMap(asRecordMap(data.rejections), "rejected_at"),
      executions: trimMap(executions, "requested_at"),
      history: Array.isArray(data.history) ? data.history.slice(-MAX_HISTORY_EVENTS) : [],
    };
  } catch {
    return emptyExecutionStore();
  }
}

export function saveExecutionStore(store) {
  try {
    const safe = {
      version: 1,
      approvals: trimMap(asRecordMap(store?.approvals), "approved_at"),
      rejections: trimMap(asRecordMap(store?.rejections), "rejected_at"),
      executions: trimMap(asRecordMap(store?.executions), "requested_at"),
      history: Array.isArray(store?.history) ? store.history.slice(-MAX_HISTORY_EVENTS) : [],
    };
    localStorage.setItem(EXEC_STORAGE_KEY, JSON.stringify(safe));
  } catch {
    // Quota or privacy mode: the app keeps working for this session.
  }
}

export function getApproval(store, planId, taskId) {
  return store?.approvals?.[execKey(planId, taskId)] || null;
}

export function getExecution(store, planId, taskId) {
  return store?.executions?.[execKey(planId, taskId)] || null;
}

// approved (fresh) | stale (edited since approval) | rejected | none
export function approvalState(store, planId, task, actionType, apiParams) {
  const key = execKey(planId, task.id);
  const approval = store?.approvals?.[key] || null;
  const rejected = store?.rejections?.[key] || null;
  if (approval) {
    const fresh = snapshotsEqual(approval.snapshot, buildSnapshot(task, actionType, apiParams));
    return { state: fresh ? "approved" : "stale", approval };
  }
  if (rejected) return { state: "rejected", approval: null };
  return { state: "none", approval: null };
}

// Dependency states for the backend context (planning + execution + local).
export function depStates(entry, store, planId) {
  return (entry?.tasks || []).map((task) => {
    const record = store?.executions?.[execKey(planId, task.id)];
    return {
      id: task.id,
      planning_status: effectiveStatus(task, entry),
      execution_status: record?.status || "none",
      local: entry?.localStatus?.[task.id] ?? null,
    };
  });
}

// Client-side preflight mirroring the backend gates (backend re-checks all).
export function readiness(store, entry, task, actionType, apiParams, { running } = {}) {
  const reasons = [];
  const approval = approvalState(store, entry.id, task, actionType, apiParams);
  if (approval.state === "rejected") reasons.push("Task was rejected.");
  else if (approval.state === "stale") reasons.push("Task changed since approval: approve it again.");
  else if (approval.state !== "approved") reasons.push("Approval is required before execution.");
  if (task.status === "blocked") reasons.push("Task is blocked: resolve its blockers first.");
  // Planning status only: manual local tracking (done/skipped checkboxes) is
  // not a real execution and must never block Execute. Mirrors the backend
  // gate, which only sees the planning status sent in the request payload.
  if (task.status === "done" || task.status === "skipped") {
    reasons.push("Task is already finished.");
  }
  if (Array.isArray(task.needs_info) && task.needs_info.length > 0) {
    reasons.push(`Missing prerequisites (${task.needs_info.join(", ")}).`);
  }
  const states = Object.fromEntries(depStates(entry, store, entry.id).map((dep) => [dep.id, dep]));
  for (const depId of task.depends_on || []) {
    const dep = states[depId];
    if (!dep) {
      reasons.push(`Unknown dependency '${depId}'.`);
    } else if (dep.execution_status === "failed" || dep.execution_status === "interrupted") {
      reasons.push(`Dependency '${depId}' failed.`);
    } else if (!(dep.execution_status === "completed" || dep.local === "done" || dep.local === "skipped")) {
      reasons.push(`Dependency '${depId}' is not completed yet.`);
    }
  }
  const existing = getExecution(store, entry.id, task.id);
  if (existing && existing.status === "completed") reasons.push("Already executed successfully.");
  if (running) reasons.push("Another task is already executing.");
  return { ok: reasons.length === 0, reasons };
}

function withHistory(store, event) {
  const history = [...(store.history || []), event].slice(-MAX_HISTORY_EVENTS);
  return { ...store, history };
}

export function approveTaskState(store, planId, task, taskTitle, actionType, apiParams) {
  const key = execKey(planId, task.id);
  const approvals = { ...store.approvals };
  const rejections = { ...store.rejections };
  delete rejections[key];
  approvals[key] = {
    snapshot: buildSnapshot(task, actionType, apiParams),
    approved_at: new Date().toISOString(),
  };
  return withHistory(
    { ...store, approvals, rejections },
    {
      ts: new Date().toISOString(),
      plan_id: planId,
      task_id: task.id,
      task_title: taskTitle,
      event: "approved",
      detail: actionType,
    }
  );
}

export function rejectTaskState(store, planId, taskId, taskTitle) {
  const key = execKey(planId, taskId);
  const approvals = { ...store.approvals };
  const rejections = { ...store.rejections };
  delete approvals[key];
  rejections[key] = { rejected_at: new Date().toISOString() };
  return withHistory(
    { ...store, approvals, rejections },
    {
      ts: new Date().toISOString(),
      plan_id: planId,
      task_id: taskId,
      task_title: taskTitle,
      event: "rejected",
      detail: "",
    }
  );
}

export function revokeTaskState(store, planId, taskId, taskTitle) {
  const key = execKey(planId, taskId);
  const approvals = { ...store.approvals };
  delete approvals[key];
  return withHistory(
    { ...store, approvals },
    {
      ts: new Date().toISOString(),
      plan_id: planId,
      task_id: taskId,
      task_title: taskTitle,
      event: "revoked",
      detail: "",
    }
  );
}

export function startExecutionState(store, planId, task, actionType, idempotencyKey) {
  const key = execKey(planId, task.id);
  const previous = store.executions?.[key];
  const executions = {
    ...store.executions,
    [key]: {
      execution_id: "",
      idempotency_key: idempotencyKey,
      action_type: actionType,
      status: "running",
      task_title: task.title,
      requested_at: new Date().toISOString(),
      started_at: "",
      finished_at: "",
      result_summary: "",
      result: null,
      error: "",
      attempts: (previous?.attempts || 0) + 1,
      fingerprint: "",
    },
  };
  return withHistory(
    { ...store, executions },
    {
      ts: new Date().toISOString(),
      plan_id: planId,
      task_id: task.id,
      task_title: task.title,
      event: "started",
      detail: actionType,
    }
  );
}

export function completeExecutionState(store, planId, taskId, taskTitle, data) {
  const key = execKey(planId, taskId);
  const previous = store.executions?.[key] || {};
  const executions = {
    ...store.executions,
    [key]: {
      ...previous,
      execution_id: data.execution_id || "",
      status: "completed",
      started_at: data.started_at || "",
      finished_at: data.finished_at || "",
      result_summary: data.result_summary || "",
      result: data.result || null,
      error: "",
      fingerprint: data.fingerprint || "",
    },
  };
  return withHistory(
    { ...store, executions },
    {
      ts: new Date().toISOString(),
      plan_id: planId,
      task_id: taskId,
      task_title: taskTitle,
      event: data.duplicate ? "duplicate" : "completed",
      detail: data.result_summary || "",
    }
  );
}

function finishExecutionState(store, planId, taskId, taskTitle, status, error) {
  const key = execKey(planId, taskId);
  const previous = store.executions?.[key] || {};
  const executions = {
    ...store.executions,
    [key]: {
      ...previous,
      status,
      finished_at: new Date().toISOString(),
      error: error || "",
    },
  };
  return withHistory(
    { ...store, executions },
    {
      ts: new Date().toISOString(),
      plan_id: planId,
      task_id: taskId,
      task_title: taskTitle,
      event: status,
      detail: error || "",
    }
  );
}

export function failExecutionState(store, planId, taskId, taskTitle, error) {
  return finishExecutionState(store, planId, taskId, taskTitle, "failed", error);
}

export function cancelExecutionState(store, planId, taskId, taskTitle) {
  return finishExecutionState(store, planId, taskId, taskTitle, "cancelled", "Cancelled by user.");
}

export function clearHistory(store) {
  return { ...store, history: [] };
}
