// Capture photo volontaire via la caméra : une image fixe, jamais de vidéo
// enregistrée. Aucune dépendance : getUserMedia + canvas suffisent.
// Le JPEG produit respecte le contrat /chat (signature valide, ≤ 5 Mo).

export const CAMERA_MAX_DIMENSION = 1920;
export const CAMERA_JPEG_QUALITY = 0.85;
export const CAMERA_MIME = "image/jpeg";

export function isCameraSupported() {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function" &&
    typeof document !== "undefined" &&
    typeof document.createElement === "function"
  );
}

function cameraError(error) {
  const name = error && error.name ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return new Error("Camera access denied: allow the camera, then try again.");
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return new Error("No camera detected on this device.");
  }
  return new Error("Camera unavailable: check your browser, then try again.");
}

// Démarre la prévisualisation sur un élément <video> fourni par l'appelant.
// À appeler uniquement depuis un clic explicite. Retourne
// { stop(), capture() }. capture() fige une frame en JPEG redimensionné.
// Le flux n'est jamais conservé : stop() après capture, fermeture ou erreur.
export async function startCameraPreview(video) {
  if (!isCameraSupported()) {
    throw new Error("Your browser does not allow camera access.");
  }
  if (!video || typeof video !== "object") {
    throw new Error("Camera preview unavailable: try again.");
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  } catch (error) {
    throw cameraError(error);
  }
  let released = false;
  function stop() {
    if (released) return;
    released = true;
    try {
      video.pause();
    } catch {
      // Élément déjà détaché.
    }
    try {
      video.srcObject = null;
    } catch {
      // Source déjà retirée.
    }
    stream.getTracks().forEach((track) => {
      try {
        track.stop();
      } catch {
        // Piste déjà arrêtée.
      }
    });
  }
  try {
    video.srcObject = stream;
    video.muted = true;
    const played = video.play();
    if (played && typeof played.catch === "function") {
      await played.catch(() => {});
    }
  } catch (error) {
    stop();
    throw new Error("Camera preview failed: try again.");
  }

  async function capture() {
    if (released) {
      throw new Error("Camera closed: reopen the preview, then try again.");
    }
    const width = video.videoWidth || 0;
    const height = video.videoHeight || 0;
    if (!width || !height) {
      throw new Error("Empty image: wait for the preview, then try again.");
    }
    const scale = Math.min(1, CAMERA_MAX_DIMENSION / Math.max(width, height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("Capture is not possible in this browser.");
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => {
      try {
        canvas.toBlob(resolve, CAMERA_MIME, CAMERA_JPEG_QUALITY);
      } catch {
        resolve(null);
      }
    });
    if (!blob || blob.size === 0) {
      throw new Error("Empty capture: try again.");
    }
    return blob;
  }

  return { stop, capture };
}
