// Enregistrement vocal et encodage WAV mono PCM 16 bits à 24 kHz.
// Aucune dépendance : MediaRecorder, Web Audio et OfflineAudioContext suffisent.
// Le WAV produit respecte le contrat Meta Voice Transcribe (mono, 16 bits, 24 kHz).

export const VOICE_SAMPLE_RATE = 24000;
export const VOICE_MAX_SECONDS = 600;
export const VOICE_MAX_BYTES = 32 * 1024 * 1024;

export function isVoiceSupported() {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function" &&
    typeof MediaRecorder !== "undefined" &&
    typeof AudioContext !== "undefined" &&
    typeof OfflineAudioContext !== "undefined"
  );
}

function microphoneError(error) {
  const name = error && error.name ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return new Error("Microphone access denied: allow the microphone, then try again.");
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return new Error("No microphone detected on this device.");
  }
  return new Error("Microphone unavailable: check your browser, then try again.");
}

// Enregistre le micro puis convertit en WAV à l'arrêt.
// Retourne { stop(): Promise<Blob>, cancel() }. Arrêt automatique à 10 minutes.
export async function startVoiceCapture({ onTick } = {}) {
  if (!isVoiceSupported()) {
    throw new Error("Your browser does not support audio recording.");
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (error) {
    throw microphoneError(error);
  }

  const chunks = [];
  let recorder;
  try {
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunks.push(event.data);
    };
    recorder.start(250);
  } catch (error) {
    // Le flux est déjà acquis : libérer le micro avant de signaler l'échec.
    // Aucune autre ressource n'existe encore (ni timer, ni arrêt auto).
    stream.getTracks().forEach((track) => track.stop());
    throw new Error("Recording interrupted: try again.");
  }

  const startedAt = Date.now();
  const timer = setInterval(() => {
    if (typeof onTick === "function") onTick((Date.now() - startedAt) / 1000);
  }, 250);

  let finished = null;
  let cancelled = false;

  function cleanup() {
    clearInterval(timer);
    clearTimeout(autoStop);
    stream.getTracks().forEach((track) => track.stop());
  }

  function finish() {
    if (!finished) {
      finished = new Promise((resolve, reject) => {
        recorder.onstop = async () => {
          cleanup();
          if (cancelled) {
            reject(new Error("Recording canceled."));
            return;
          }
          try {
            const raw = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
            resolve(await convertToWav(raw));
          } catch (error) {
            reject(error);
          }
        };
        recorder.onerror = () => {
          cleanup();
          reject(new Error("Recording interrupted: try again."));
        };
        try {
          recorder.stop();
        } catch (error) {
          cleanup();
          reject(new Error("Recording interrupted: try again."));
        }
      });
    }
    return finished;
  }

  const autoStop = setTimeout(finish, VOICE_MAX_SECONDS * 1000);

  return {
    stop: finish,
    cancel() {
      cancelled = true;
      cleanup();
      if (recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // Déjà arrêté : rien à faire.
        }
      }
    },
  };
}

// Décode un enregistrement puis le rééchantillonne en mono 24 kHz avant
// de l'encoder en WAV PCM 16 bits. Tout se fait en mémoire.
async function convertToWav(sourceBlob) {
  const bytes = await sourceBlob.arrayBuffer();
  const decoder = new AudioContext();
  let decoded;
  try {
    decoded = await decoder.decodeAudioData(bytes);
  } catch {
    throw new Error("Cannot read the recording: try again.");
  } finally {
    if (typeof decoder.close === "function") {
      decoder.close().catch(() => {});
    }
  }
  if (!decoded.duration || decoded.duration > VOICE_MAX_SECONDS) {
    throw new Error("Recording too long: 10 minutes maximum.");
  }
  const length = Math.max(1, Math.ceil(decoded.duration * VOICE_SAMPLE_RATE));
  const offline = new OfflineAudioContext(1, length, VOICE_SAMPLE_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const rendered = await offline.startRendering();
  const blob = encodeWav(rendered.getChannelData(0), VOICE_SAMPLE_RATE);
  if (blob.size > VOICE_MAX_BYTES) {
    throw new Error("Audio too large: 10 minutes or 32 MB maximum.");
  }
  return blob;
}

// Construit un WAV mono PCM 16 bits (en-tête de 44 octets + échantillons).
function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function writeAscii(view, offset, text) {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}
