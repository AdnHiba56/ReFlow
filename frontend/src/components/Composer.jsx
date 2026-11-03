import { useEffect, useRef, useState } from "react";
import { transcribeAudio } from "../api.js";
import { isVoiceSupported, startVoiceCapture } from "../lib/audio.js";
import { isSpeechPlaying, isSpeechSupported, onSpeechChange, stopSpeech } from "../lib/speech.js";
import { CameraIcon, ImageIcon, MicIcon, SendIcon } from "./icons.jsx";
import { CAMERA_MIME, isCameraSupported, startCameraPreview } from "../lib/camera.js";

const MAX_CHARS = 2000;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const IMAGE_MIME_TYPES = [
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/gif",
  "image/webp",
  "image/x-icon",
];
const IMAGE_ACCEPT = [...IMAGE_MIME_TYPES, ".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico"].join(
  ","
);
// Repli quand le navigateur ne renseigne pas file.type (certains .ico).
const EXTENSION_MIME = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
};

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const rest = String(total % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}

export default function Composer({
  id,
  placeholder,
  sending,
  sendingLabel,
  onSend,
  autoFocus,
  seed,
  allowImage = false,
  onImageChange = null,
  imageEpoch = 0,
  onGenerateImage = null,
  allowGenerateImage = false,
  onGenerateModeChange = null,
  generateEpoch = 0,
  onEditImage = null,
  allowEditImage = false,
  onSegmentImage = null,
  allowSegmentImage = false,
  // Graine image pour ré-éditer un résultat : { key, image }. Mémoire uniquement.
  imageSeed = null,
}) {
  const [draft, setDraft] = useState("");
  const [voice, setVoice] = useState("idle");
  const [voiceError, setVoiceError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [speaking, setSpeaking] = useState(() => isSpeechSupported() && isSpeechPlaying());
  // Image sélectionnée : { mime, data (Base64 sans préfixe), previewUrl, name }.
  // Mémoire uniquement : le Base64 ne part que vers le backend, jamais au stockage.
  const [image, setImage] = useState(null);
  const [imageError, setImageError] = useState("");
  const [converting, setConverting] = useState(false);
  // Caméra : null (fermée), "starting" (autorisation), "live" (aperçu).
  // Démarrage uniquement sur clic explicite, jamais automatique.
  const [camera, setCamera] = useState(null);
  // Mode génération d'image : l'envoi part vers POST /image au lieu du chat.
  // Réarmé à chaque envoi (chaque génération est un acte volontaire).
  const [generateMode, setGenerateMode] = useState(false);
  // Mode édition d'image : l'envoi part vers POST /image/edit avec l'image
  // sélectionnée conservée. Mutuellement exclusif avec le mode génération.
  const [editMode, setEditMode] = useState(false);
  // Mode segmentation : l'envoi part vers POST /segment avec le concept
  // saisi et l'image conservée. Exclusif avec génération et édition.
  const [segmentMode, setSegmentMode] = useState(false);
  const areaRef = useRef(null);
  const captureRef = useRef(null);
  const fileInputRef = useRef(null);
  const previewUrlRef = useRef(null);
  const videoRef = useRef(null);
  const cameraHandleRef = useRef(null);
  // Miroir du callback parent, appelable depuis le nettoyage de démontage.
  const imageChangeRef = useRef(null);
  imageChangeRef.current = onImageChange;
  const voiceSupported = isVoiceSupported();

  function notifyImageChange(value) {
    if (typeof onImageChange === "function") onImageChange(value);
  }

  const generateModeChangeRef = useRef(null);
  generateModeChangeRef.current = onGenerateModeChange;

  function notifyGenerateMode(value) {
    if (typeof onGenerateModeChange === "function") onGenerateModeChange(value);
  }

  // Pré-remplit le champ quand une suggestion est choisie (puis redonne le focus).
  useEffect(() => {
    if (seed) {
      setDraft(seed.text);
      areaRef.current?.focus();
    }
  }, [seed]);

  // Libère le micro si le composant disparaît pendant un enregistrement.
  useEffect(() => {
    const ref = captureRef;
    return () => {
      ref.current?.cancel();
      ref.current = null;
    };
  }, []);

  // Démontage : le miroir parent du mode Génération est invalidé pour
  // qu'un tour vocal ultérieur ne génère pas sur un état fantôme.
  useEffect(() => {
    const ref = generateModeChangeRef;
    return () => {
      if (typeof ref.current === "function") ref.current(false);
    };
  }, []);

  // Libère l'aperçu d'image non envoyé si le composant disparaît,
  // et invalide le miroir parent (l'URL révoquée ne doit pas survivre).
  useEffect(() => {
    const ref = previewUrlRef;
    const changeRef = imageChangeRef;
    return () => {
      if (ref.current) {
        URL.revokeObjectURL(ref.current);
        ref.current = null;
      }
      if (typeof changeRef.current === "function") changeRef.current(null);
    };
  }, []);

  // Consommation par la voix : le parent incrémente imageEpoch, le Composer
  // se vide. Transfert comme à l'envoi : oubli sans révocation, le message
  // vocal étant devenu propriétaire de l'URL.
  const epochRef = useRef(imageEpoch);
  useEffect(() => {
    if (epochRef.current === imageEpoch) return;
    epochRef.current = imageEpoch;
    previewUrlRef.current = null;
    setImage(null);
    setImageError("");
  }, [imageEpoch]);

  // Attache le flux caméra quand le panneau (avec <video>) est monté.
  // Annulation sûre si l'utilisateur ferme pendant la demande d'autorisation.
  useEffect(() => {
    if (camera !== "starting") return undefined;
    let cancelled = false;
    startCameraPreview(videoRef.current)
      .then((handle) => {
        if (cancelled) {
          handle.stop();
          return;
        }
        cameraHandleRef.current = handle;
        setCamera("live");
      })
      .catch((error) => {
        if (!cancelled) {
          setImageError(error instanceof Error ? error.message : "Camera unavailable: try again.");
          setCamera(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [camera]);

  // Libère le flux caméra au démontage.
  useEffect(() => {
    const ref = cameraHandleRef;
    return () => {
      ref.current?.stop();
      ref.current = null;
    };
  }, []);

  // Ferme la caméra si le mode Discussion est quitté (sélecteur masqué).
  useEffect(() => {
    if (!allowImage && cameraHandleRef.current) {
      cameraHandleRef.current.stop();
      cameraHandleRef.current = null;
      setCamera(null);
    }
  }, [allowImage]);

  // Quitte le mode génération si la Discussion est quittée.
  useEffect(() => {
    if (!allowGenerateImage) {
      setGenerateMode(false);
      notifyGenerateMode(false);
    }
  }, [allowGenerateImage]);

  // Quitte le mode édition si la Discussion est quittée.
  useEffect(() => {
    if (!allowEditImage) setEditMode(false);
  }, [allowEditImage]);

  // Quitte le mode segmentation si la Discussion est quittée.
  useEffect(() => {
    if (!allowSegmentImage) setSegmentMode(false);
  }, [allowSegmentImage]);

  // Ré-édition d'un résultat : le parent fournit les octets mémoire via
  // imageSeed. Adoption comme une sélection normale, puis mode édition.
  const imageSeedRef = useRef(null);
  useEffect(() => {
    if (!imageSeed || imageSeedRef.current === imageSeed.key) return;
    imageSeedRef.current = imageSeed.key;
    revokePreview();
    previewUrlRef.current = imageSeed.image.previewUrl;
    setImage(imageSeed.image);
    setImageError("");
    notifyImageChange(imageSeed.image);
    setGenerateMode(false);
    notifyGenerateMode(false);
    setEditMode(true);
    areaRef.current?.focus();
  }, [imageSeed]);

  // Consommation par la voix : le parent incrémente generateEpoch, le
  // Composer décoche la bascule (réarmement comme après un envoi écrit).
  const generateEpochRef = useRef(generateEpoch);
  useEffect(() => {
    if (generateEpochRef.current === generateEpoch) return;
    generateEpochRef.current = generateEpoch;
    setGenerateMode(false);
  }, [generateEpoch]);

  // Suit la lecture vocale pour verrouiller le micro (jamais d'enregistrement
  // pendant une lecture) ; le micro reste manuel, jamais réactivé seul.
  useEffect(() => {
    if (!isSpeechSupported()) return undefined;
    const update = () => setSpeaking(isSpeechPlaying());
    update();
    return onSpeechChange(update);
  }, []);

  const cameraOpen = camera !== null;
  const generating = allowGenerateImage && generateMode;
  const editing = allowEditImage && editMode && image !== null;
  const segmenting = allowSegmentImage && segmentMode && image !== null;
  const canSend =
    !sending &&
    voice === "idle" &&
    !converting &&
    !cameraOpen &&
    (generating
      ? draft.trim().length > 0
      : editing || segmenting
        ? draft.trim().length > 0 && image !== null
        : draft.trim().length > 0 || image !== null);

  // Bascule génération : la génération n'accepte aucune image d'entrée,
  // toute sélection ou aperçu caméra en cours est donc abandonné proprement.
  function toggleGenerateMode() {
    if (sending || voice !== "idle" || converting) return;
    if (!generateMode) {
      clearImage();
      closeCamera();
      setEditMode(false);
      setSegmentMode(false);
    }
    const next = !generateMode;
    setGenerateMode(next);
    notifyGenerateMode(next);
  }

  // Bascule édition : exige une image sélectionnée, conservée pendant le
  // mode. Exclut la génération (qui refuse toute image d'entrée).
  function toggleEditMode() {
    if (sending || voice !== "idle" || converting || image === null) return;
    if (!editMode && generateMode) {
      setGenerateMode(false);
      notifyGenerateMode(false);
    }
    setEditMode(!editMode);
    if (!editMode) setSegmentMode(false);
  }

  // Bascule segmentation : exige une image sélectionnée, conservée pendant
  // le mode. Exclut la génération et l'édition.
  function toggleSegmentMode() {
    if (sending || voice !== "idle" || converting || image === null) return;
    if (!segmentMode) {
      if (generateMode) {
        setGenerateMode(false);
        notifyGenerateMode(false);
      }
      setEditMode(false);
    }
    setSegmentMode(!segmentMode);
  }
  const micStopsSpeech = speaking && voice === "idle";

  function revokePreview() {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
  }

  function clearImage() {
    revokePreview();
    setImage(null);
    setImageError("");
    setEditMode(false);
    setSegmentMode(false);
    notifyImageChange(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  // Convertit un Blob (fichier ou capture caméra) vers l'état image
  // partagé { mime, data, previewUrl, name }. L'appelant révoque l'ancien
  // aperçu et notifie le parent.
  function blobToImage(blob, mime, name) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result;
        if (typeof dataUrl !== "string" || !dataUrl.includes(",")) {
          reject(new Error("Could not read the file."));
          return;
        }
        resolve({
          mime,
          data: dataUrl.slice(dataUrl.indexOf(",") + 1),
          previewUrl: URL.createObjectURL(blob),
          name,
        });
      };
      reader.onerror = () => reject(new Error("Could not read the file."));
      reader.readAsDataURL(blob);
    });
  }

  function resolveMime(file) {
    const declared = typeof file.type === "string" ? file.type.toLowerCase() : "";
    if (IMAGE_MIME_TYPES.includes(declared)) return declared;
    const name = typeof file.name === "string" ? file.name.toLowerCase() : "";
    const dot = name.lastIndexOf(".");
    if (dot >= 0 && EXTENSION_MIME[name.slice(dot)]) return EXTENSION_MIME[name.slice(dot)];
    return "";
  }

  async function handleFileChange(event) {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const mime = resolveMime(file);
    if (!mime) {
      setImageError("Accepted formats: JPEG, PNG, GIF, WebP, or ICO.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setImageError("Image too large: 5 MB maximum.");
      return;
    }
    setConverting(true);
    setImageError("");
    try {
      const selected = await blobToImage(file, mime, file.name || "image");
      revokePreview();
      previewUrlRef.current = selected.previewUrl;
      setImage(selected);
      notifyImageChange(selected);
      areaRef.current?.focus();
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "Image unreadable: try again.");
    } finally {
      setConverting(false);
    }
  }

  function openCamera() {
    if (!allowImage || sending || converting || camera !== null) return;
    if (!isCameraSupported()) {
      setImageError("Your browser does not allow camera access.");
      return;
    }
    setImageError("");
    setCamera("starting");
  }

  function closeCamera() {
    if (cameraHandleRef.current) {
      cameraHandleRef.current.stop();
      cameraHandleRef.current = null;
    }
    setCamera(null);
  }

  async function capturePhoto() {
    const handle = cameraHandleRef.current;
    if (!handle || converting) return;
    setConverting(true);
    setImageError("");
    try {
      const blob = await handle.capture();
      // Libération immédiate après la frame figée, avant conversion.
      closeCamera();
      if (blob.size > MAX_IMAGE_BYTES) {
        throw new Error("Image too large: 5 MB maximum.");
      }
      const photo = await blobToImage(blob, CAMERA_MIME, "Camera photo");
      revokePreview();
      previewUrlRef.current = photo.previewUrl;
      setImage(photo);
      notifyImageChange(photo);
      areaRef.current?.focus();
    } catch (error) {
      // Erreur de capture : flux déjà fermé, panneau replié.
      closeCamera();
      setImageError(error instanceof Error ? error.message : "Capture failed: try again.");
    } finally {
      setConverting(false);
    }
  }

  async function toggleVoice() {
    if (sending || voice === "working") return;
    if (voice === "recording") {
      const handle = captureRef.current;
      captureRef.current = null;
      if (!handle) {
        setVoice("idle");
        return;
      }
      setVoice("working");
      setVoiceError("");
      try {
        const wav = await handle.stop();
        const text = await transcribeAudio(wav);
        // La transcription remplit le champ : l'utilisateur relit puis envoie lui-même.
        setDraft(text);
        areaRef.current?.focus();
      } catch (error) {
        setVoiceError(error instanceof Error ? error.message : "Transcription failed: try again.");
      } finally {
        setVoice("idle");
      }
      return;
    }
    if (speaking) {
      // La lecture s'arrête d'abord ; un second clic démarrera la capture.
      stopSpeech();
      return;
    }
    setVoiceError("");
    try {
      const handle = await startVoiceCapture({ onTick: (value) => setElapsed(value) });
      captureRef.current = handle;
      setElapsed(0);
      setVoice("recording");
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : "Microphone unavailable: try again.");
      setVoice("idle");
    }
  }

  async function submit(event) {
    if (event) event.preventDefault();
    if (!canSend) return;
    const text = draft.trim();
    if (generating && typeof onGenerateImage === "function") {
      setDraft("");
      // Chaque génération est volontaire : le mode se réarme après envoi.
      setGenerateMode(false);
      notifyGenerateMode(false);
      await onGenerateImage(text);
      return;
    }
    const outgoing = image;
    const wasEditing = editing && typeof onEditImage === "function";
    if (wasEditing) setEditMode(false);
    const wasSegmenting = segmenting && typeof onSegmentImage === "function";
    if (wasSegmenting) setSegmentMode(false);
    setDraft("");
    // Transfert de l'aperçu à la conversation : le Composer oublie l'URL
    // sans la révoquer (la conversation la libère à la suppression).
    previewUrlRef.current = null;
    setImage(null);
    setImageError("");
    notifyImageChange(null);
    if (wasEditing) {
      await onEditImage(text, outgoing);
      return;
    }
    if (wasSegmenting) {
      await onSegmentImage(text, outgoing);
      return;
    }
    await onSend(text, outgoing);
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <label className="field-label" htmlFor={id}>
        Ta demande
      </label>
      <textarea
        id={id}
        ref={areaRef}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={
          generating
            ? "Describe the image to generate…"
            : editing
              ? "Describe the edit to make…"
              : segmenting
                ? "Describe the object to isolate…"
                : placeholder
        }
        rows={3}
        maxLength={MAX_CHARS}
        disabled={sending}
        autoFocus={autoFocus}
      />
      <div className="composer-row">
        <span className="counter" aria-live="polite">
          {MAX_CHARS - draft.length} characters left
        </span>
        {voice !== "idle" && (
          <span className="voice-status" aria-live="polite">
            {voice === "recording" ? (
              <>
                <span className="voice-dot" aria-hidden="true" />
                Écoute… {formatElapsed(elapsed)} — clique pour arrêter
              </>
            ) : (
              "Transcription en cours…"
            )}
          </span>
        )}
        {speaking && voice === "idle" && (
          <span className="voice-status" aria-live="polite">
            Playing…
          </span>
        )}
        <span className="composer-buttons">
          {allowGenerateImage && (
            <button
              type="button"
              className={generating ? "secondary small active" : "secondary small"}
              onClick={toggleGenerateMode}
              disabled={sending || voice !== "idle" || converting}
              title="Generate an image from the description"
              aria-label="Turn image generation on or off"
              aria-pressed={generating}
            >
              Image
            </button>
          )}
          {allowEditImage && !generating && (
            <button
              type="button"
              className={editing ? "secondary small active" : "secondary small"}
              onClick={toggleEditMode}
              disabled={sending || voice !== "idle" || converting || image === null}
              title="Edit the selected image from the instruction"
              aria-label="Turn image editing on or off"
              aria-pressed={editing}
            >
              Edit
            </button>
          )}
          {allowSegmentImage && !generating && (
            <button
              type="button"
              className={segmenting ? "secondary small active" : "secondary small"}
              onClick={toggleSegmentMode}
              disabled={sending || voice !== "idle" || converting || image === null}
              title="Segment the described object in the selected image"
              aria-label="Turn image segmentation on or off"
              aria-pressed={segmenting}
            >
              Segment
            </button>
          )}
          {allowImage && !generating && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept={IMAGE_ACCEPT}
                onChange={handleFileChange}
                hidden
                aria-hidden="true"
                tabIndex={-1}
              />
              <button
                type="button"
                className="icon-button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || converting || cameraOpen}
                title="Attach an image"
                aria-label="Attach an image (JPEG, PNG, GIF, WebP, or ICO, 5 MB maximum)"
              >
                <ImageIcon />
              </button>
              <button
                type="button"
                className="icon-button"
                onClick={openCamera}
                disabled={sending || converting || cameraOpen}
                title="Take a photo"
                aria-label="Take a photo with the camera"
              >
                <CameraIcon />
              </button>
            </>
          )}
          <button
            type="button"
            className={voice === "recording" ? "icon-button recording" : "icon-button"}
            onClick={toggleVoice}
            disabled={sending || voice === "working" || !voiceSupported}
            title={
              voice === "recording"
                ? "Stop and transcribe"
                : micStopsSpeech
                  ? "Stop playback"
                  : "Dictate a voice message"
            }
            aria-label={
              voice === "recording"
                ? "Stop recording and transcribe"
                : micStopsSpeech
                  ? "Stop voice playback"
                  : "Dictate a voice message"
            }
            aria-pressed={voice === "recording"}
          >
            <MicIcon />
          </button>
          <button
            type="submit"
            className="primary send"
            disabled={!canSend}
            aria-label={
              sending ? sendingLabel : generating ? "Generate the image" : editing ? "Edit the image" : segmenting ? "Segment the image" : "Send the message"
            }
          >
            <SendIcon />
            <span aria-hidden="true">
              {sending ? sendingLabel : generating ? "Generate" : editing ? "Edit" : segmenting ? "Segment" : "Send"}
            </span>
          </button>
        </span>
      </div>
      {allowImage && image && (
        <div className="image-preview">
          <img src={image.previewUrl} alt={`Preview: ${image.name}`} />
          <span className="image-name">{image.name}</span>
          <button
            type="button"
            className="secondary small"
            onClick={clearImage}
            disabled={sending}
            aria-label="Remove the selected image"
          >
            Remove
          </button>
        </div>
      )}
      {allowImage && cameraOpen && (
        <div className="camera-panel">
          <video
            ref={videoRef}
            className="camera-video"
            muted
            autoPlay
            playsInline
            aria-label="Camera preview"
          />
          {camera === "starting" && (
            <p className="muted" aria-live="polite">
              Connecting to the camera…
            </p>
          )}
          <span className="camera-buttons">
            {camera === "live" && (
              <button
                type="button"
                className="primary small"
                onClick={capturePhoto}
                disabled={converting}
                aria-label="Capture a photo"
              >
                Capture
              </button>
            )}
            <button
              type="button"
              className="secondary small"
              onClick={closeCamera}
              aria-label={
                camera === "starting"
                  ? "Cancel the camera request"
                  : "Close the camera without capturing"
              }
            >
              {camera === "starting" ? "Cancel" : "Close"}
            </button>
          </span>
        </div>
      )}
      {allowImage && converting && (
        <p className="muted" aria-live="polite">
          Preparing the image…
        </p>
      )}
      {voiceError && (
        <p className="voice-error" role="alert">
          {voiceError}
        </p>
      )}
      {imageError && (
        <p className="image-error" role="alert">
          {imageError}
        </p>
      )}
      <p className="hint">
        {voiceSupported
          ? "Microphone: click to dictate, click again to transcribe. Enter to send, Shift+Enter for a new line."
          : "Your browser does not support voice dictation. Enter to send, Shift+Enter for a new line."}
      </p>
    </form>
  );
}
