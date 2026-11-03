// Conversion Base64 → URL objet pour l'affichage mémoire des images
// générées. L'appelant possède l'URL retournée et la révoque quand le
// message est supprimé. Le Base64 ne transite que vers l'affichage,
// jamais vers le stockage persistant.

export function blobUrlFromBase64(mime, data) {
  let binary;
  try {
    if (typeof data !== "string" || !data) throw new Error("Empty image data.");
    binary = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
  } catch {
    throw new Error("Received image is unusable: try again.");
  }
  return URL.createObjectURL(new Blob([binary], { type: mime }));
}
