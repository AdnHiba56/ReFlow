// Session vocale explicite : un bouton démarre, un bouton arrête.
// Enchaîne microphone → transcription → discussion → synthèse, tour par tour,
// strictement dans l'ordre des identifiants de tour. Barge-in : pendant la
// synthèse, la VAD renforcée (seuil relevé, confirmation 500 ms, délai
// anti-écho) détecte une vraie voix humaine, coupe la synthèse via
// stopSpeech() et traite l'intervention en file, sans doublon ni désordre.
// Réutilise micStream (capture + VAD), api.js (transcribeAudio, sendChat)
// et speech.js (speakText) sans les modifier.
// Aucun log de contenu : ni audio, ni transcriptions, ni réponses.
// Les erreurs remontent via onError avec des messages sûrs uniquement.
import { generateImage, sendChat, transcribeAudio } from "../api.js";
import { isMicStreamSupported, startMicSession } from "./micStream.js";
import {
  isSpeechPlaying,
  isSpeechSupported,
  onSpeechChange,
  speakText,
  stopSpeech,
} from "./speech.js";

export const VOICE_HISTORY_MAX = 12;
// Limite backend : 2 000 caractères par message transmis à POST /chat.
// Appliquée à l'historique envoyé uniquement, jamais au texte affiché.
export const VOICE_TEXT_MAX = 2000;

let activeSession = false;

export function isVoiceSessionActive() {
  return activeSession;
}

// Démarre une session vocale. À appeler uniquement depuis une action
// explicite (bouton), qui débloque aussi le contexte audio. Retourne
// { stop() }. L'historique vient de getHistory() (-> [{ role, text }],
// ordre chronologique) ; les tours affichables partent vers
// onUserTranscript(turn, text) et onAssistantReply(turn, text).
// États via onState : "listening", "thinking", "speaking", "interrupted",
// "stopped". "interrupted" = barge-in détecté, synthèse coupée, l'intervention
// est en file. getImage() optionnel : image one-shot jointe au tour courant
// ({ mime, data, previewUrl }), consommée juste avant sendChat.
// consumeImageMode() optionnel : mode génération one-shot ; si le tour le
// consomme (true), la transcription part vers generateImage au lieu de
// sendChat (un seul appel), et le résultat arrive via onAssistantImage.
export async function startVoiceSession(options = {}) {
  const {
    getHistory = () => [],
    getImage = null,
    consumeImageMode = null,
    onUserTranscript = null,
    onAssistantReply = null,
    onAssistantImage = null,
    onState = null,
    onError = null,
  } = options;
  if (activeSession) throw new Error("A voice session is already active.");
  if (typeof getHistory !== "function") {
    throw new Error("startVoiceSession requires a getHistory callback.");
  }
  if (!isMicStreamSupported()) {
    throw new Error("Your browser does not support voice conversation.");
  }
  if (!isSpeechSupported()) {
    throw new Error("Your browser does not support voice playback.");
  }
  activeSession = true;

  let stopped = false;
  let speaking = false;
  let chain = Promise.resolve();
  let mic = null;

  const emitState = (state) => {
    if (typeof onState !== "function") return;
    try {
      onState(state);
    } catch {
      // Un abonné défaillant ne doit pas casser la session.
    }
  };
  const reportError = (error) => {
    if (typeof onError !== "function") return;
    try {
      onError(error);
    } catch {
      // Un abonné défaillant ne doit pas casser la session.
    }
  };
  const safeCall = (fn, ...args) => {
    if (typeof fn !== "function") return;
    try {
      fn(...args);
    } catch (error) {
      reportError(error instanceof Error ? error : new Error("Display error."));
    }
  };

  // Fin de lecture = la synthèse ne joue plus. stopSpeech() déclenche aussi
  // cet état, donc l'attente se termine toujours, y compris à l'arrêt.
  function waitForSpeechEnd() {
    return new Promise((resolve) => {
      if (!isSpeechPlaying()) {
        resolve();
        return;
      }
      const off = onSpeechChange((playing) => {
        if (!playing) {
          off();
          resolve();
        }
      });
    });
  }

  // Lecture d'une réponse (texte discuté ou confirmation de génération) :
  // chemin vocal unique pour les deux modes (barge-in, file, attentes).
  async function speakReply(textToSpeak) {
    if (stopped) return;
    speaking = true;
    emitState("speaking");
    // Barge-in armé AVANT la synthèse : le délai anti-écho couvre le début
    // de la voix. Désarmé dans tous les cas (fin, erreur, interruption, arrêt).
    if (mic) mic.setBargeMode(true);
    try {
      if (speakText(textToSpeak)) await waitForSpeechEnd();
    } catch (error) {
      if (!stopped) {
        reportError(error instanceof Error ? error : new Error("Voice playback failed."));
      }
    } finally {
      speaking = false;
      if (mic) mic.setBargeMode(false);
    }
    if (!stopped) emitState("listening");
  }

  async function processTurn(blob, meta) {
    if (stopped) return;
    emitState("thinking");
    let text;
    try {
      text = await transcribeAudio(blob);
    } catch (error) {
      if (!stopped) {
        reportError(error instanceof Error ? error : new Error("Transcription failed."));
        emitState("listening");
      }
      return;
    }
    if (stopped) return;
    // Routage du tour après transcription : génération d'image (one-shot)
    // ou discussion. Un seul appel Meta par tour, jamais les deux.
    let imageMode = false;
    if (typeof consumeImageMode === "function") {
      try {
        imageMode = consumeImageMode() === true;
      } catch {
        imageMode = false;
      }
    }
    if (imageMode) {
      safeCall(onUserTranscript, meta.turn, text, null);
      let generated;
      try {
        generated = await generateImage(text);
      } catch (error) {
        if (!stopped) {
          reportError(error instanceof Error ? error : new Error("Generation failed."));
          emitState("listening");
        }
        return;
      }
      if (stopped) return;
      safeCall(onAssistantImage, meta.turn, text, generated);
      await speakReply("Here is your image.");
      return;
    }
    let history;
    try {
      const prior = getHistory();
      // Tour courant ajouté une seule fois, en fin de liste, puis chaque
      // texte borné à la limite backend avant l'envoi.
      history = [...(Array.isArray(prior) ? prior : []), { role: "user", text }]
        .map((entry) => ({
          role: entry.role,
          text:
            typeof entry.text === "string"
              ? entry.text.slice(0, VOICE_TEXT_MAX)
              : "",
        }))
        .slice(-VOICE_HISTORY_MAX);
    } catch (error) {
      if (!stopped) {
        reportError(error instanceof Error ? error : new Error("History unavailable."));
        emitState("listening");
      }
      return;
    }
    // Image one-shot : consommée ici, au dernier moment avant sendChat.
    // Aucun await entre cette lecture et l'envoi : un arrêt avant l'envoi
    // la laisse disponible, et elle ne part jamais deux fois. Sans getImage,
    // le tour reste strictement textuel, comme avant.
    let turnPreview = null;
    if (typeof getImage === "function" && !stopped) {
      let taken = null;
      try {
        taken = getImage() || null;
      } catch {
        taken = null;
      }
      if (
        taken &&
        typeof taken === "object" &&
        typeof taken.mime === "string" &&
        typeof taken.data === "string" &&
        taken.data
      ) {
        const current = history[history.length - 1];
        history[history.length - 1] = {
          ...current,
          image: { mime: taken.mime, data: taken.data },
        };
        if (typeof taken.previewUrl === "string") turnPreview = taken.previewUrl;
      }
    }
    // Snapshot pris avant notification : même si l'appelant pousse le message
    // dans l'état de façon synchrone, l'historique envoyé ne le duplique pas.
    safeCall(onUserTranscript, meta.turn, text, turnPreview);
    let reply;
    try {
      reply = await sendChat(history);
    } catch (error) {
      if (!stopped) {
        reportError(error instanceof Error ? error : new Error("Reply failed."));
        emitState("listening");
      }
      return;
    }
    if (stopped) return;
    safeCall(onAssistantReply, meta.turn, reply);
    await speakReply(reply);
  }

  function handleTurn(blob, meta) {
    if (stopped) return;
    if (speaking) {
      // Barge-in : prise de parole confirmée (forte + soutenue, seuils
      // renforcés micStream) pendant notre synthèse. Coupe la voix, repasse
      // la VAD en mode normal, puis traite l'intervention en file comme un
      // tour ordinaire : le tour interrompu termine proprement (stopSpeech
      // résout waitForSpeechEnd), l'ordre est préservé, sans doublon.
      // Si la synthèse venait de finir (course), stopSpeech est sans effet.
      emitState("interrupted");
      stopSpeech();
      speaking = false;
      if (mic) mic.setBargeMode(false);
    }
    // File strictement ordonnée : chaque tour attend la fin complète
    // (transcription + réponse + lecture) du précédent. Jamais deux voix.
    // Le catch de survie garantit que la file ne meurt jamais silencieusement.
    chain = chain
      .then(() => processTurn(blob, meta))
      .catch(() => {
        speaking = false;
        if (!stopped) {
          reportError(new Error("Voice turn interrupted."));
          emitState("listening");
        }
      });
  }

  try {
    stopSpeech();
    mic = await startMicSession({
      onTurn: handleTurn,
      onError: (error) => {
        if (!stopped) reportError(error);
      },
    });
  } catch (error) {
    activeSession = false;
    throw error;
  }

  let stopPromise = null;
  async function doStop() {
    stopped = true;
    try {
      stopSpeech();
      if (mic) await mic.stop();
    } finally {
      activeSession = false;
    }
    emitState("stopped");
  }

  emitState("listening");
  return {
    // Idempotent : arrêts multiples sans effet. Les résultats tardifs
    // (transcription, réponse, lecture en cours) sont ignorés après l'arrêt.
    stop() {
      if (!stopPromise) stopPromise = doStop();
      return stopPromise;
    },
  };
}
