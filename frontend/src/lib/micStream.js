// Capture microphone continue avec détection de fin de parole (VAD),
// pour les sessions vocales. Chaque prise de parole produit un WAV mono
// PCM 16 bits à 24 kHz, transmis au callback onTurn.
// Principes repris d'audio.js (sans le modifier) : même contrat WAV,
// rééchantillonnage via OfflineAudioContext, discipline des ressources.
// Aucune dépendance, aucun réseau : getUserMedia + Web Audio uniquement.
// Le microphone ne démarre que sur appel explicite de startMicSession().
// Nécessite un contexte sécurisé (localhost ou HTTPS).

export const MIC_SAMPLE_RATE = 24000;
export const TURN_SILENCE_MS = 800; // silence marquant la fin d'une prise de parole
export const TURN_ONSET_MS = 150; // parole confirmée : ignore clics et bruits brefs
export const TURN_PREROLL_MS = 250; // audio conservé avant le début détecté
export const TURN_MAX_SECONDS = 60; // borne anti-accumulation par prise de parole
export const SPEECH_RMS = 0.02; // seuil de parole (avec noiseSuppression actif)
// Barge-in (pendant notre synthèse) : seule une voix humaine proche et
// soutenue déclenche, jamais l'écho résiduel post-AEC. Seuils volontaires
// conservateurs : un faux négatif vaut mieux qu'une boucle audio.
export const BARGE_RMS = 0.06; // 3x le seuil normal : écho << voix proche
export const BARGE_ONSET_MS = 500; // confirmation vocale continue exigée
export const BARGE_COOLDOWN_MS = 350; // délai anti-écho après début synthèse
const POST_FRAMES = 2048; // échantillons par message du capteur

export function isMicStreamSupported() {
  // Vérification statique : AudioWorklet lui-même est testé à l'exécution,
  // avec repli ScriptProcessor, car sa disponibilité dépend du navigateur
  // (y compris le chargement du module depuis une URL Blob).
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function" &&
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

// Code du capteur inline : pas de fichier AudioWorklet séparé (chargé via URL Blob).
// Moyenne les canaux en mono et poste des blocs PCM Float32 (tampon transféré).
const WORKLET_CODE = `
class ReflowMicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = [];
    this.pendingLen = 0;
  }
  process(inputs) {
    const channels = (inputs && inputs[0]) || [];
    if (channels.length > 0 && channels[0].length > 0) {
      const len = channels[0].length;
      const mono = new Float32Array(len);
      for (let i = 0; i < len; i += 1) {
        let sum = 0;
        for (let c = 0; c < channels.length; c += 1) sum += channels[c][i];
        mono[i] = sum / channels.length;
      }
      this.pending.push(mono);
      this.pendingLen += len;
      if (this.pendingLen >= ${POST_FRAMES}) {
        const out = new Float32Array(this.pendingLen);
        let offset = 0;
        for (const block of this.pending) {
          out.set(block, offset);
          offset += block.length;
        }
        this.pending = [];
        this.pendingLen = 0;
        this.port.postMessage(out, [out.buffer]);
      }
    }
    return true;
  }
}
registerProcessor("reflow-mic-capture", ReflowMicCapture);
`;

async function attachWorklet(ctx, source, onChunk) {
  if (!ctx.audioWorklet) throw new Error("AudioWorklet unavailable.");
  const url = URL.createObjectURL(new Blob([WORKLET_CODE], { type: "application/javascript" }));
  try {
    await ctx.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }
  const node = new AudioWorkletNode(ctx, "reflow-mic-capture");
  // Gain nul : garantit l'exécution du graphe sans réinjecter le micro.
  const mute = ctx.createGain();
  mute.gain.value = 0;
  node.port.onmessage = (event) => {
    if (event.data instanceof Float32Array) onChunk(event.data);
  };
  source.connect(node);
  node.connect(mute);
  mute.connect(ctx.destination);
  return () => {
    try {
      node.port.close();
    } catch {
      // Déjà fermé.
    }
    try {
      node.disconnect();
    } catch {
      // Déjà déconnecté.
    }
    try {
      mute.disconnect();
    } catch {
      // Déjà déconnecté.
    }
  };
}

function attachScriptProcessor(ctx, source, onChunk) {
  // Repli déprécié mais universel si AudioWorklet est inutilisable.
  if (typeof ctx.createScriptProcessor !== "function") {
    throw new Error("Continuous capture is unavailable in this browser.");
  }
  const proc = ctx.createScriptProcessor(POST_FRAMES, 1, 1);
  const mute = ctx.createGain();
  mute.gain.value = 0;
  proc.onaudioprocess = (event) => {
    onChunk(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(proc);
  proc.connect(mute);
  mute.connect(ctx.destination);
  return () => {
    try {
      proc.onaudioprocess = null;
    } catch {
      // Déjà détaché.
    }
    try {
      proc.disconnect();
    } catch {
      // Déjà déconnecté.
    }
    try {
      mute.disconnect();
    } catch {
      // Déjà déconnecté.
    }
  };
}

function rmsOf(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  return samples.length > 0 ? Math.sqrt(sum / samples.length) : 0;
}

function concatFloat32(parts, total) {
  const out = new Float32Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

// Construit un WAV mono PCM 16 bits (en-tête de 44 octets + échantillons).
// Même disposition qu'audio.js, réimplémentée ici pour garder ce module isolé.
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

// Démarre une session de capture : à appeler uniquement depuis une action
// explicite (bouton). Retourne { stop(), setBargeMode() }. Les prises de
// parole arrivent via onTurn(blob, { turn, seconds, truncated }). États via
// onState : "listening" (attend la parole), "capturing" (tour en cours),
// "stopped". setBargeMode(true) arme la détection renforcée pendant la
// synthèse (seuil relevé, confirmation longue, délai anti-écho).
export async function startMicSession(options = {}) {
  const {
    onTurn,
    onState,
    onError,
    silenceMs = TURN_SILENCE_MS,
    onsetMs = TURN_ONSET_MS,
    prerollMs = TURN_PREROLL_MS,
    maxTurnSeconds = TURN_MAX_SECONDS,
    threshold = SPEECH_RMS,
    bargeThreshold = BARGE_RMS,
    bargeOnsetMs = BARGE_ONSET_MS,
    bargeCooldownMs = BARGE_COOLDOWN_MS,
  } = options;
  if (typeof onTurn !== "function") {
    throw new Error("startMicSession requires an onTurn callback.");
  }
  if (!isMicStreamSupported()) {
    throw new Error("Your browser does not support continuous audio capture.");
  }

  const emitState = (state) => {
    if (typeof onState !== "function") return;
    try {
      onState(state);
    } catch {
      // Un abonné défaillant ne doit pas casser la capture.
    }
  };
  const reportError = (error) => {
    if (typeof onError !== "function") return;
    try {
      onError(error);
    } catch {
      // Un abonné défaillant ne doit pas casser la capture.
    }
  };

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    throw microphoneError(error);
  }

  const stopTracks = () => {
    stream.getTracks().forEach((track) => {
      try {
        track.stop();
      } catch {
        // Piste déjà arrêtée.
      }
    });
  };

  let ctx;
  try {
    // Fréquence demandée à titre indicatif : le taux réel est relu ci-dessous
    // et chaque prise de parole est rééchantillonnée si nécessaire.
    ctx = new AudioContext({ sampleRate: MIC_SAMPLE_RATE });
    if (ctx.state === "suspended") {
      await ctx.resume().catch(() => {});
    }
  } catch (error) {
    stopTracks();
    throw new Error("Audio unavailable: try again.");
  }
  if (ctx.state !== "running") {
    // Contexte suspendu malgré la reprise : session sourde, refuser proprement.
    stopTracks();
    try {
      await ctx.close();
    } catch {
      // Contexte déjà fermé.
    }
    throw new Error("Audio suspended by the browser: try again.");
  }

  const inputRate = ctx.sampleRate > 0 ? ctx.sampleRate : MIC_SAMPLE_RATE;
  let source;

  let stopped = false;
  let listening = true;
  let encoding = false;
  let turnParts = [];
  let turnLen = 0;
  let prerollParts = [];
  let prerollLen = 0;
  const prerollMax = Math.max(1, Math.ceil((prerollMs / 1000) * inputRate));
  let silenceAcc = 0;
  let onsetAcc = 0;
  let turnSeq = 0;
  let bargeMode = false;
  let bargeCooldownUntil = 0;

  function resetTurn() {
    turnParts = [];
    turnLen = 0;
    prerollParts = [];
    prerollLen = 0;
    silenceAcc = 0;
    onsetAcc = 0;
    listening = true;
  }

  async function resampleTo24k(pcm) {
    if (inputRate === MIC_SAMPLE_RATE) return pcm;
    const buffer = ctx.createBuffer(1, pcm.length, inputRate);
    buffer.copyToChannel(pcm, 0);
    const targetLen = Math.max(1, Math.ceil((pcm.length / inputRate) * MIC_SAMPLE_RATE));
    const offline = new OfflineAudioContext(1, targetLen, MIC_SAMPLE_RATE);
    const playback = offline.createBufferSource();
    playback.buffer = buffer;
    playback.connect(offline.destination);
    playback.start();
    const rendered = await offline.startRendering();
    return rendered.getChannelData(0);
  }

  async function finishTurn(truncated) {
    const parts = turnParts;
    const total = turnLen;
    resetTurn();
    emitState("listening");
    if (stopped || total === 0) return;
    turnSeq += 1;
    const turn = turnSeq;
    encoding = true;
    try {
      const pcm = concatFloat32(parts, total);
      const samples = await resampleTo24k(pcm);
      if (stopped) return;
      const blob = encodeWav(samples, MIC_SAMPLE_RATE);
      if (stopped) return;
      // Non bloquant : la VAD reprend dès l'encodage terminé, sans attendre
      // le traitement réseau ou IA du tour par l'appelant.
      Promise.resolve()
        .then(() => {
          if (stopped) return undefined;
          return onTurn(blob, { turn, seconds: samples.length / MIC_SAMPLE_RATE, truncated });
        })
        .catch((error) => {
          if (!stopped) {
            reportError(
              error instanceof Error ? error : new Error("Unusable speech turn.")
            );
          }
        });
    } catch (error) {
      if (!stopped) {
        reportError(
          error instanceof Error ? error : new Error("Audio conversion failed: try again.")
        );
      }
    } finally {
      encoding = false;
    }
  }

  function onPcm(chunk) {
    if (stopped || encoding || !(chunk instanceof Float32Array) || chunk.length === 0) return;
    const chunkMs = (chunk.length / inputRate) * 1000;
    // Mode barge-in (pendant notre synthèse) : seuil relevé, car seule une
    // voix humaine proche dépasse nettement l'écho résiduel post-AEC.
    const speech = rmsOf(chunk) >= (bargeMode ? bargeThreshold : threshold);
    if (listening) {
      // Pré-écoute : conserve une amorce, ignore les silences initiaux.
      prerollParts.push(chunk);
      prerollLen += chunk.length;
      while (prerollLen > prerollMax && prerollParts.length > 1) {
        const dropped = prerollParts.shift();
        prerollLen -= dropped.length;
      }
      // Délai anti-écho : l'amorce continue de se remplir, mais aucune
      // parole n'est confirmée juste après le début de la synthèse.
      const cooling = bargeMode && Date.now() < bargeCooldownUntil;
      if (!speech || cooling) {
        onsetAcc = 0;
        return;
      }
      onsetAcc += chunkMs;
      if (onsetAcc < (bargeMode ? bargeOnsetMs : onsetMs)) return;
      // Parole confirmée : le tour démarre avec son amorce, sans les silences d'avant.
      turnParts = prerollParts;
      turnLen = prerollLen;
      prerollParts = [];
      prerollLen = 0;
      onsetAcc = 0;
      silenceAcc = 0;
      listening = false;
      emitState("capturing");
      return;
    }
    turnParts.push(chunk);
    turnLen += chunk.length;
    if (speech) {
      silenceAcc = 0;
    } else {
      silenceAcc += chunkMs;
    }
    if (silenceAcc >= silenceMs) {
      finishTurn(false);
    } else if (turnLen / inputRate >= maxTurnSeconds) {
      finishTurn(true);
    }
  }

  let detachSink = () => {};
  try {
    source = ctx.createMediaStreamSource(stream);
    try {
      detachSink = await attachWorklet(ctx, source, onPcm);
    } catch (workletError) {
      detachSink = attachScriptProcessor(ctx, source, onPcm);
    }
  } catch (error) {
    stopTracks();
    try {
      await ctx.close();
    } catch {
      // Contexte déjà fermé.
    }
    throw new Error("Continuous capture is unavailable in this browser.");
  }

  let stopPromise = null;
  async function doStop() {
    stopped = true;
    try {
      detachSink();
    } catch {
      // Capteur déjà détaché.
    }
    try {
      source.disconnect();
    } catch {
      // Source déjà déconnectée.
    }
    stopTracks();
    try {
      await ctx.close();
    } catch {
      // Contexte déjà fermé.
    }
    emitState("stopped");
  }

  // Bascule la détection renforcée autour de la synthèse. À l'activation :
  // délai anti-écho + remise à zéro de la confirmation (pas de résidu).
  // Idempotent, sans effet après l'arrêt.
  function setBargeMode(active) {
    if (stopped) return;
    const next = active === true;
    if (next && !bargeMode) {
      bargeCooldownUntil = Date.now() + bargeCooldownMs;
    }
    onsetAcc = 0;
    bargeMode = next;
  }

  emitState("listening");
  return {
    // Idempotent : arrêts multiples sans effet.
    stop() {
      if (!stopPromise) stopPromise = doStop();
      return stopPromise;
    },
    setBargeMode,
  };
}
