// Adresse de l'API backend. En développement, Vite redirige /api
// vers le serveur FastAPI (voir proxy dans vite.config.js).
const API_BASE_URL = "/api";

function messageForStatus(status) {
  if (status === 422) return "Your request is empty or exceeds 2000 characters.";
  if (status === 429) return "Quota reached: wait a moment before retrying.";
  if (status === 413) return "Audio too large: 10 minutes or 32 MB maximum.";
  if (status === 503 || status === 504)
    return "The Meta service is not responding right now. Try again in a moment.";
  if (status >= 500) return "The server ran into a problem. Try again.";
  if (status === 404) return "Service not found: check that the backend is running.";
  return "An unexpected error occurred.";
}

export async function generatePlan(requestText) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: requestText }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    // Le backend renvoie des messages sûrs : on les préfère quand ils existent.
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  return response.json();
}

export async function requestAgentPlan(requestText) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/plan/structured`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: requestText }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  if (
    !data ||
    typeof data !== "object" ||
    typeof data.goal !== "string" ||
    !data.goal ||
    !Array.isArray(data.tasks) ||
    data.tasks.length === 0
  ) {
    throw new Error("Structured plan is unusable: try again.");
  }
  return data;
}

export async function fetchActions() {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/actions`);
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    throw new Error(messageForStatus(response.status));
  }
  const data = await response.json();
  if (!data || !Array.isArray(data.actions) || data.actions.length === 0) {
    throw new Error("Actions list is unusable: try again.");
  }
  return data.actions;
}

export async function executeTask(payload, { signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error && error.name === "AbortError") throw error;
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  if (!data || typeof data !== "object" || typeof data.result_summary !== "string") {
    throw new Error("Execution reply is unusable: try again.");
  }
  return data;
}

export async function transcribeAudio(audioBlob) {
  let response;
  try {
    // Corps brut audio/wav : aucun multipart entrant, donc aucun fichier temporaire serveur.
    response = await fetch(`${API_BASE_URL}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: audioBlob,
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    // Le backend renvoie des messages sûrs : on les préfère quand ils existent.
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  const transcript = typeof data?.transcript === "string" ? data.transcript.trim() : "";
  if (!transcript) {
    throw new Error("Empty transcription: try again.");
  }
  return transcript;
}

export async function sendChat(messages) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    // Le backend renvoie des messages sûrs : on les préfère quand ils existent.
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  const reply = typeof data?.reply === "string" ? data.reply.trim() : "";
  if (!reply) {
    throw new Error("Empty reply: try again.");
  }
  return reply;
}

export async function generateImage(prompt) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/image`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  if (typeof data?.data !== "string" || !data.data || typeof data?.mime !== "string") {
    throw new Error("Received image is unusable: try again.");
  }
  return { mime: data.mime, data: data.data, mediaId: mediaIdOf(data) };
}

export function mediaIdOf(data) {
  return typeof data?.media_id === "string" && data.media_id ? data.media_id : null;
}

// Stable backend reference for a persisted generated/edited image.
// Served by GET /media/{id} (via the /api proxy); never a blob: URL.
export function mediaUrl(mediaId) {
  return `${API_BASE_URL}/media/${mediaId}`;
}

export async function segmentImage(concept, image) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/segment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ concept, image: { mime: image.mime, data: image.data } }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  if (
    !data ||
    typeof data !== "object" ||
    !Number.isFinite(data.image?.width) ||
    !Number.isFinite(data.image?.height) ||
    data.image.width <= 0 ||
    data.image.height <= 0 ||
    !Array.isArray(data.matches)
  ) {
    throw new Error("Received segmentation is unusable: try again.");
  }
  return { image: data.image, matches: data.matches, truncated: data.truncated === true };
}

export async function uploadDocument({ filename, mime, data }) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/documents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, mime, data }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const payload = await response.json();
      if (typeof payload?.Detail === "string") detail = payload.Detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  return response.json();
}

export async function fetchDocument(docId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/documents/${docId}`);
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const payload = await response.json();
      if (typeof payload?.Detail === "string") detail = payload.Detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  return response.json();
}

export async function analyzeDocument(docId, { operation, question = "", style = "simple", language = "en" }) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/documents/${encodeURIComponent(docId)}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation, question, style, language }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const payload = await response.json();
      if (typeof payload?.Detail === "string") detail = payload.Detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  if (!data || typeof data !== "object" || typeof data.result !== "string") {
    throw new Error("Analysis reply is unusable: try again.");
  }
  return data;
}

export async function editImage(prompt, image) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/image/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, image: { mime: image.mime, data: image.data } }),
    });
  } catch {
    throw new Error("Cannot reach the server. Check that the backend is running.");
  }
  if (!response.ok) {
    let detail = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      detail = null;
    }
    throw new Error(detail || messageForStatus(response.status));
  }
  const data = await response.json();
  if (typeof data?.data !== "string" || !data.data || typeof data?.mime !== "string") {
    throw new Error("Received image is unusable: try again.");
  }
  return { mime: data.mime, data: data.data, mediaId: mediaIdOf(data) };
}
