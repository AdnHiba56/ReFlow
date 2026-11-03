// Helpers de lecture tolérante partagés par les blocs.
// Fichier indépendant : évite toute dépendance circulaire avec BlockRenderer.jsx.
export function textArray(value, max = 30) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => typeof item === "string").slice(0, max);
}

export function objectArray(value, max = 30) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => item && typeof item === "object" && !Array.isArray(item)).slice(0, max);
}

export function textOf(value, fallback = "") {
  return typeof value === "string" ? value : fallback;
}
