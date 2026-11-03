// Stockage local des plans structurés (POST /plan/structured).
// Clé dédiée : les anciens plans (reflow:plans:v1) ne sont ni lus ni écrasés.
// Le suivi local (localStatus) ne prétend à aucune mise à jour backend.
import { sanitizeBlocks } from "../components/blocks/BlockRenderer.jsx";
import { asStringArray, uid } from "./plans.js";

const STORAGE_KEY = "reflow:agent-plans:v1";
const MAX_STORED_AGENT_PLANS = 30;

export const TASK_STATUSES = ["pending", "ready", "blocked", "done", "skipped"];
export const PLAN_STATUSES = ["draft", "needs_info", "ready"];

export const TASK_STATUS_LABELS = {
  pending: "Pending",
  ready: "Ready",
  blocked: "Blocked",
  done: "Done",
  skipped: "Skipped",
};

export const PLAN_STATUS_LABELS = {
  draft: "Draft",
  needs_info: "Missing info",
  ready: "Ready",
};

function normalizeTask(raw) {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" ? raw.id : "";
  const title = typeof raw.title === "string" ? raw.title : "";
  if (!id || !title) return null;
  return {
    id,
    title,
    detail: typeof raw.detail === "string" ? raw.detail : "",
    depends_on: asStringArray(raw.depends_on),
    success_criteria: asStringArray(raw.success_criteria),
    needs_info: asStringArray(raw.needs_info),
    status: TASK_STATUSES.includes(raw.status) ? raw.status : "pending",
  };
}

function normalizeMissing(raw) {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" ? raw.id : "";
  const question = typeof raw.question === "string" ? raw.question : "";
  if (!id || !question) return null;
  return {
    id,
    field: typeof raw.field === "string" ? raw.field : "",
    question,
    blocks_task_ids: asStringArray(raw.blocks_task_ids),
  };
}

function normalizeLocalStatus(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const clean = {};
  for (const [key, value] of Object.entries(raw)) {
    if (value === "done" || value === "skipped") clean[key] = value;
  }
  return clean;
}

export function normalizeAgentPlanEntry(entry) {
  if (!entry || typeof entry !== "object") return null;
  const tasks = Array.isArray(entry.tasks)
    ? entry.tasks.map(normalizeTask).filter(Boolean)
    : [];
  if (typeof entry.id !== "string" || typeof entry.goal !== "string" || tasks.length === 0) {
    return null;
  }
  return {
    id: entry.id,
    request: typeof entry.request === "string" ? entry.request : "",
    goal: entry.goal,
    summary: typeof entry.summary === "string" ? entry.summary : "",
    tasks,
    missing_information: Array.isArray(entry.missing_information)
      ? entry.missing_information.map(normalizeMissing).filter(Boolean)
      : [],
    questions: asStringArray(entry.questions),
    status: PLAN_STATUSES.includes(entry.status) ? entry.status : "draft",
    blocks: sanitizeBlocks(entry.blocks),
    localStatus: normalizeLocalStatus(entry.localStatus),
    createdAt: typeof entry.createdAt === "number" ? entry.createdAt : 0,
  };
}

export function createAgentPlanEntry(requestText, data) {
  const entry = normalizeAgentPlanEntry({
    id: uid(),
    request: requestText,
    goal: data?.goal,
    summary: data?.summary,
    tasks: data?.tasks,
    missing_information: data?.missing_information,
    questions: data?.questions,
    status: data?.status,
    blocks: data?.blocks,
    localStatus: {},
    createdAt: Date.now(),
  });
  // Le backend garantit goal + tâches ; repli défensif si la réponse dérive.
  if (entry) return entry;
  return {
    id: uid(),
    request: requestText,
    goal: typeof data?.goal === "string" && data.goal ? data.goal : "Plan without a goal",
    summary: "",
    tasks: [],
    missing_information: [],
    questions: [],
    status: "draft",
    blocks: [],
    localStatus: {},
    createdAt: Date.now(),
  };
}

export function loadAgentPlans() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeAgentPlanEntry).filter(Boolean);
  } catch {
    return [];
  }
}

export function saveAgentPlans(plans) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(plans.slice(0, MAX_STORED_AGENT_PLANS)));
  } catch {
    // Stockage indisponible ou plein : l'application reste utilisable en mémoire.
  }
}

// Statut effectif : le suivi local (done/skipped cochés par l'utilisateur)
// prime sur le statut serveur, sans jamais le réécrire côté backend.
export function effectiveStatus(task, entry) {
  const local = entry?.localStatus?.[task.id];
  if (local === "done" || local === "skipped") return local;
  return TASK_STATUSES.includes(task.status) ? task.status : "pending";
}

export function agentProgressOf(entry) {
  const tasks = Array.isArray(entry?.tasks) ? entry.tasks : [];
  if (tasks.length === 0) return 0;
  const finished = tasks.filter((task) => {
    const status = effectiveStatus(task, entry);
    return status === "done" || status === "skipped";
  }).length;
  return Math.round((finished / tasks.length) * 100);
}
