// Conversations conservées localement dans le navigateur.
// Seules les conversations réellement créées par l'utilisateur sont stockées.
import { uid } from "./plans.js";

const STORAGE_KEY = "reflow:conversations:v1";
const MAX_STORED = 30;

export function createConversation(firstText) {
  const clean = typeof firstText === "string" ? firstText.trim() : "";
  return {
    id: uid(),
    title: clean ? clean.slice(0, 60) : "New conversation",
    mode: "plan",
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

function normalizeMessage(message) {
  return {
    id: typeof message.id === "string" ? message.id : uid(),
    role: message.role === "assistant" ? "assistant" : "user",
    kind:
      message.kind === "plan" ||
      message.kind === "error" ||
      message.kind === "image" ||
      message.kind === "segment"
        ? message.kind
        : "text",
    text: typeof message.text === "string" ? message.text : "",
    entryId: typeof message.entryId === "string" ? message.entryId : null,
    imagePrompt: typeof message.imagePrompt === "string" ? message.imagePrompt : "",
    segmentPrompt: typeof message.segmentPrompt === "string" ? message.segmentPrompt : "",
    mediaId: typeof message.mediaId === "string" && message.mediaId ? message.mediaId : null,
    mediaMime: typeof message.mediaMime === "string" ? message.mediaMime : "",
    edited: message.edited === true,
    previousText: typeof message.previousText === "string" ? message.previousText : "",
  };
}

export function findMessageIndex(messages, messageId) {
  return (messages || []).findIndex((m) => m && m.id === messageId);
}

// Kind of the reply directly following a user message ("text" by default),
// or null when no assistant reply follows it.
export function replyKindAfter(messages, userIndex) {
  const next = (messages || [])[userIndex + 1];
  if (!next || next.role !== "assistant") return null;
  return next.kind === "plan" || next.kind === "error" || next.kind === "image" || next.kind === "segment"
    ? next.kind
    : "text";
}

// Pure text edit: replaces the user text in place (previous text kept in
// `previousText`) and designates the immediate reply for regeneration.
// Never truncates: later messages are untouched. Returns null when the
// message cannot be edited (unknown id, assistant message, empty text).
export function applyTextEdit(messages, messageId, newText) {
  const clean = typeof newText === "string" ? newText.trim() : "";
  if (!clean) return null;
  const index = findMessageIndex(messages, messageId);
  const target = (messages || [])[index];
  if (!target || target.role !== "user") return null;
  const next = (messages || []).map((message, i) =>
    i === index ? { ...message, text: clean, edited: true, previousText: target.text || "" } : message
  );
  const replyIndex =
    index + 1 < next.length && next[index + 1].role === "assistant" ? index + 1 : -1;
  return { messages: next, userIndex: index, replyIndex, replyKind: replyKindAfter(messages, index) };
}

// History strictly before the edited message: later messages must never
// leak into the regenerated reply.
export function historyBefore(messages, userIndex) {
  return (messages || []).slice(0, Math.max(0, userIndex));
}

// Replaces the immediate reply in place (same id) or inserts it after the
// edited message when there was none. Later messages are untouched.
export function replaceReplyAt(messages, edit, fresh, newId) {
  const next = (messages || []).slice();
  if (
    edit.replyIndex >= 0 &&
    next[edit.replyIndex] &&
    next[edit.replyIndex].role === "assistant"
  ) {
    next[edit.replyIndex] = { id: next[edit.replyIndex].id, role: "assistant", ...fresh };
  } else {
    next.splice(edit.userIndex + 1, 0, { id: newId, role: "assistant", ...fresh });
  }
  return next;
}

function isValidConversation(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    typeof value.id === "string" &&
    Array.isArray(value.messages)
  );
}

function normalizeConversation(value) {
  return {
    id: value.id,
    title: typeof value.title === "string" && value.title ? value.title : "Conversation",
    mode: value.mode === "chat" ? "chat" : "plan",
    messages: value.messages
      .filter((m) => m && typeof m === "object" && typeof m.id === "string")
      .map(normalizeMessage)
      .slice(0, 200),
    createdAt: typeof value.createdAt === "number" ? value.createdAt : 0,
    updatedAt: typeof value.updatedAt === "number" ? value.updatedAt : 0,
  };
}

export function loadConversations() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isValidConversation)
      .map(normalizeConversation)
      .sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    return [];
  }
}

// Retire les données d'image éphémères (aperçus blob:, payloads de réessai)
// avant persistance : le Base64 et les URL temporaires ne vont jamais
// dans localStorage. Au rechargement, normalizeMessage les ignore déjà.
export function stripImageData(conversations) {
  return conversations.map((conversation) => ({
    ...conversation,
    messages: (conversation.messages || []).map((message) => {
      if (!message || typeof message !== "object") return message;
      if (
        !message.imagePreview &&
        !message.imageMime &&
        !message.retryImage &&
        !message.segmentImage &&
        !message.segmentMatches
      )
        return message;
      const clean = { ...message };
      delete clean.imagePreview;
      delete clean.imageMime;
      delete clean.retryImage;
      delete clean.segmentImage;
      delete clean.segmentMatches;
      delete clean.segmentTruncated;
      return clean;
    }),
  }));
}

export function saveConversations(conversations) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, MAX_STORED)));
  } catch {
    // Stockage indisponible : l'application reste utilisable en mémoire.
  }
}

export function searchAll(query, conversations, plans) {
  const needle = query.trim().toLowerCase();
  if (needle.length < 2) return { conversations: [], plans: [] };
  const matchingConversations = conversations.filter((conv) => {
    const haystack = [conv.title, ...conv.messages.map((m) => `${m.text || ""} ${m.imagePrompt || ""} ${m.segmentPrompt || ""}`)].join("\n").toLowerCase();
    return haystack.includes(needle);
  });
  const matchingPlans = plans.filter((plan) => {
    const haystack = [plan.title, plan.request || "", ...plan.steps, ...plan.questions].join("\n").toLowerCase();
    return haystack.includes(needle);
  });
  return { conversations: matchingConversations, plans: matchingPlans };
}
