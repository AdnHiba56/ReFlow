// Stockage local des plans générés par l'utilisateur.
// Aucune donnée fictive : seuls les plans réellement créés sont conservés,
// dans le navigateur uniquement (aucun envoi vers un serveur).
import { sanitizeBlocks } from "../components/blocks/BlockRenderer.jsx";

const STORAGE_KEY = "reflow:plans:v1";
const MAX_STORED_PLANS = 50;

export function asStringArray(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => typeof item === "string");
}

export function uid() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function createPlanEntry(requestText, data) {
  const steps = asStringArray(data?.steps);
  return {
    id: uid(),
    request: requestText,
    title: typeof data?.title === "string" && data.title ? data.title : "Untitled plan",
    steps,
    questions: asStringArray(data?.questions),
    blocks: sanitizeBlocks(data?.blocks),
    createdAt: Date.now(),
    done: steps.map(() => false),
  };
}

function isValidEntry(entry) {
  return (
    entry !== null &&
    typeof entry === "object" &&
    typeof entry.id === "string" &&
    typeof entry.title === "string" &&
    Array.isArray(entry.steps) &&
    Array.isArray(entry.questions)
  );
}

function normalizeEntry(entry) {
  const steps = asStringArray(entry.steps);
  const done = Array.isArray(entry.done) ? entry.done : [];
  return {
    id: entry.id,
    request: typeof entry.request === "string" ? entry.request : "",
    title: entry.title,
    steps,
    questions: asStringArray(entry.questions),
    // Les plans sauvegardés avant les blocs dynamiques n'en ont pas : repli classique.
    blocks: sanitizeBlocks(entry.blocks),
    createdAt: typeof entry.createdAt === "number" ? entry.createdAt : 0,
    done: steps.map((_, index) => done[index] === true),
  };
}

export function loadPlans() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidEntry).map(normalizeEntry);
  } catch {
    return [];
  }
}

export function savePlans(plans) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(plans.slice(0, MAX_STORED_PLANS)));
  } catch {
    // Stockage indisponible ou plein : l'application reste utilisable en mémoire.
  }
}

export function progressOf(entry) {
  if (!entry.steps.length) return 0;
  const checked = entry.done.filter(Boolean).length;
  return Math.round((checked / entry.steps.length) * 100);
}

export function formatDate(timestamp) {
  try {
    return new Date(timestamp).toLocaleDateString("en-US", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}
