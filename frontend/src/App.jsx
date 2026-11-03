import { useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import Dashboard from "./components/Dashboard.jsx";
import Assistant from "./components/Assistant.jsx";
import Conversations from "./components/Conversations.jsx";
import Documents from "./components/Documents.jsx";
import Plans from "./components/Plans.jsx";
import AgentPlans from "./components/AgentPlans.jsx";
import ExecutionHistory from "./components/ExecutionHistory.jsx";
import Search from "./components/Search.jsx";
import VoiceCallButton from "./components/VoiceCallButton.jsx";
import { PanelIcon, PlusIcon } from "./components/icons.jsx";
import { analyzeDocument, editImage, executeTask, fetchActions, generateImage, generatePlan, mediaUrl, requestAgentPlan, segmentImage, sendChat, uploadDocument } from "./api.js";
import { blobUrlFromBase64 } from "./lib/images.js";
import { speakPlan, stopSpeech } from "./lib/speech.js";
import { startVoiceSession } from "./lib/voiceSession.js";
import { createPlanEntry, loadPlans, savePlans } from "./lib/plans.js";
import {
  createAgentPlanEntry,
  loadAgentPlans,
  saveAgentPlans,
} from "./lib/agentPlans.js";
import {
  applyTextEdit,
  createConversation,
  historyBefore,
  loadConversations,
  replaceReplyAt,
  saveConversations,
  stripImageData,
} from "./lib/conversations.js";
import { uid } from "./lib/plans.js";
import {
  currentDoc,
  forgetDoc,
  loadDocStore,
  pushQA,
  putResult,
  rememberDoc,
  saveDocStore,
  selectDoc,
} from "./lib/documents.js";
import {
  approveTaskState,
  buildActionParams,
  cancelExecutionState,
  completeExecutionState,
  depStates,
  execKey,
  failExecutionState,
  loadExecutionStore,
  readiness,
  rejectTaskState,
  revokeTaskState,
  saveExecutionStore,
  startExecutionState,
} from "./lib/execution.js";

const VIEW_TITLES = {
  dashboard: "Home",
  assistant: "AI Assistant",
  conversations: "My conversations",
  documents: "Documents",
  plans: "My plans",
  "agent-plans": "Structured plans",
  history: "Execution history",
  search: "Search",
};

// Limite backend : 2 000 caractères par message transmis à POST /chat.
// Concerne uniquement l'historique envoyé, jamais le texte affiché.
const MAX_CHAT_TEXT_LEN = 2000;

export default function App() {
  const [view, setView] = useState("dashboard");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [plans, setPlans] = useState(loadPlans);
  const [agentPlans, setAgentPlans] = useState(loadAgentPlans);
  const [docStore, setDocStore] = useState(loadDocStore);
  const [docBusy, setDocBusy] = useState(null);
  const [executionStore, setExecutionStore] = useState(loadExecutionStore);
  const [execActions, setExecActions] = useState([]);
  const [runningExec, setRunningExec] = useState(null);
  const execAbortRef = useRef(null);
  const [conversations, setConversations] = useState(loadConversations);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [activePlanId, setActivePlanId] = useState(null);
  const [activeAgentPlanId, setActiveAgentPlanId] = useState(null);
  const [sendingId, setSendingId] = useState(null);
  const sendingRef = useRef(false);
  const [voiceStatus, setVoiceStatus] = useState("idle");
  const [voiceError, setVoiceError] = useState("");
  const voiceHandleRef = useRef(null);
  const voiceBusyRef = useRef(false);
  const voiceConversationIdRef = useRef(null);
  // Jeton de génération : chaque démarrage capture le sien ; stopVoiceCall
  // l'invalide immédiatement, y compris pendant l'attente asynchrone.
  const voiceStartTokenRef = useRef(0);
  // Image en attente partagée entre le Composer (sélection) et la voix
  // continue (consommation one-shot). Mémoire uniquement, jamais persistée.
  const pendingImageRef = useRef(null);
  // Incrémenté chaque fois que la voix consomme l'image : le Composer se vide.
  const [imageEpoch, setImageEpoch] = useState(0);
  // Miroir du mode Génération du Composer, consommé une seule fois par tour
  // vocal (même motif que l'image en attente).
  const generateModeRef = useRef(false);
  const [generateEpoch, setGenerateEpoch] = useState(0);
  // Graine de ré-édition : octets mémoire d'un résultat, jamais persistée.
  const [imageSeed, setImageSeed] = useState(null);
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;

  useEffect(() => {
    savePlans(plans);
  }, [plans]);

  useEffect(() => {
    saveAgentPlans(agentPlans);
  }, [agentPlans]);

  useEffect(() => {
    saveDocStore(docStore);
  }, [docStore]);

  useEffect(() => {
    saveExecutionStore(executionStore);
  }, [executionStore]);

  useEffect(() => {
    let cancelled = false;
    fetchActions()
      .then((list) => {
        if (!cancelled && Array.isArray(list) && list.length > 0) setExecActions(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      try {
        execAbortRef.current?.abort();
      } catch {
        /* ignore */
      }
    };
  }, []);

  useEffect(() => {
    // Les aperçus et payloads d'image sont éphémères : jamais dans localStorage.
    saveConversations(stripImageData(conversations));
  }, [conversations]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) || null;

  function touchConversation(list, conversationId, messages) {
    const now = Date.now();
    const updated = list.map((conversation) =>
      conversation.id === conversationId
        ? { ...conversation, messages, updatedAt: now }
        : conversation
    );
    updated.sort((a, b) => b.updatedAt - a.updatedAt);
    return updated;
  }

  function pushMessage(conversationId, message) {
    setConversations((prev) => {
      const target = prev.find((c) => c.id === conversationId);
      if (!target) return prev;
      let title = target.title;
      if (target.messages.length === 0 && message.role === "user" && message.text) {
        title = message.text.slice(0, 60);
      }
      const next = prev.map((conversation) =>
        conversation.id === conversationId
          ? { ...conversation, title, messages: [...conversation.messages, message] }
          : conversation
      );
      return touchConversation(
        next,
        conversationId,
        next.find((c) => c.id === conversationId).messages
      );
    });
  }

  async function generateAndStore(requestText) {
    const data = await generatePlan(requestText);
    const entry = createPlanEntry(requestText, data);
    setPlans((prev) => [entry, ...prev].slice(0, 50));
    return entry;
  }

  async function generateAndStoreAgentPlan(requestText) {
    const data = await requestAgentPlan(requestText);
    const entry = createAgentPlanEntry(requestText, data);
    setAgentPlans((prev) => [entry, ...prev].slice(0, 30));
    setActiveAgentPlanId(entry.id);
    return entry;
  }

  async function sendMessage(conversationId, text, imageInfo = null) {
    // Invalide aussi un démarrage vocal en cours (busy), pas seulement une
    // session active (handle), avant l'envoi texte.
    if (voiceHandleRef.current || voiceBusyRef.current) stopVoiceCall();
    if (sendingRef.current) return;
    sendingRef.current = true;
    pushMessage(conversationId, {
      id: uid(),
      role: "user",
      kind: "text",
      text,
      entryId: null,
      // Aperçu mémoire uniquement : dépouillé avant persistance.
      imagePreview: imageInfo?.previewUrl || null,
      imageMime: imageInfo?.mime || null,
    });
    setSendingId(conversationId);
    try {
      const conversation = conversations.find((c) => c.id === conversationId);
      if (conversation?.mode === "chat") {
        const history = (conversation.messages || [])
          .filter((m) => m.kind !== "error" && typeof m.text === "string" && m.text.trim())
          .map((m) => ({
            role: m.role === "assistant" ? "assistant" : "user",
            text: m.text.trim().slice(0, MAX_CHAT_TEXT_LEN),
          }));
        // Message courant ajouté tel quel (texte vide accepté si image jointe),
        // avec son image éventuelle ; les anciennes images ne sont jamais renvoyées.
        history.push({
          role: "user",
          text: text.trim().slice(0, MAX_CHAT_TEXT_LEN),
          ...(imageInfo ? { image: { mime: imageInfo.mime, data: imageInfo.data } } : {}),
        });
        const reply = await sendChat(history.slice(-12));
        pushMessage(conversationId, {
          id: uid(),
          role: "assistant",
          kind: "text",
          text: reply,
          entryId: null,
        });
      } else {
        const entry = await generateAndStore(text);
        pushMessage(conversationId, {
          id: uid(),
          role: "assistant",
          kind: "plan",
          text: "",
          entryId: entry.id,
        });
        // Lecture auto : annule la précédente, sans-op si synthèse indisponible.
        speakPlan(entry);
      }
    } catch (err) {
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "error",
        text: err instanceof Error ? err.message : "An unexpected error occurred.",
        entryId: null,
        retryText: text,
        // Payload de réessai, mémoire uniquement (jamais persisté).
        retryImage: imageInfo,
      });
    } finally {
      sendingRef.current = false;
      setSendingId(null);
    }
  }

  async function sendImageMessage(conversationId, prompt) {
    if (voiceHandleRef.current || voiceBusyRef.current) stopVoiceCall();
    if (sendingRef.current) return;
    sendingRef.current = true;
    pushMessage(conversationId, { id: uid(), role: "user", kind: "text", text: prompt, entryId: null });
    setSendingId(conversationId);
    try {
      const generated = await generateImage(prompt);
      const previewUrl = blobUrlFromBase64(generated.mime, generated.data);
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "image",
        text: "",
        entryId: null,
        // Prompt persisté en texte ; octets et URL en mémoire uniquement.
        imagePrompt: prompt,
        imagePreview: previewUrl,
        imageMime: generated.mime,
        mediaId: generated.mediaId || null,
        mediaMime: generated.mediaId ? generated.mime : "",
      });
    } catch (err) {
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "error",
        text: err instanceof Error ? err.message : "An unexpected error occurred.",
        entryId: null,
        retryText: prompt,
        retryKind: "image",
      });
    } finally {
      sendingRef.current = false;
      setSendingId(null);
    }
  }

  async function sendEditImageMessage(conversationId, prompt, imageInfo) {
    if (!imageInfo) return;
    if (voiceHandleRef.current || voiceBusyRef.current) stopVoiceCall();
    if (sendingRef.current) return;
    sendingRef.current = true;
    pushMessage(conversationId, {
      id: uid(),
      role: "user",
      kind: "text",
      text: prompt,
      entryId: null,
      imagePreview: imageInfo.previewUrl || null,
      imageMime: imageInfo.mime || null,
    });
    setSendingId(conversationId);
    try {
      const edited = await editImage(prompt, { mime: imageInfo.mime, data: imageInfo.data });
      const previewUrl = blobUrlFromBase64(edited.mime, edited.data);
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "image",
        text: "",
        entryId: null,
        imagePrompt: prompt,
        imagePreview: previewUrl,
        imageMime: edited.mime,
        mediaId: edited.mediaId || null,
        mediaMime: edited.mediaId ? edited.mime : "",
      });
    } catch (err) {
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "error",
        text: err instanceof Error ? err.message : "An unexpected error occurred.",
        entryId: null,
        retryText: prompt,
        retryKind: "edit",
        retryImage: imageInfo,
      });
    } finally {
      sendingRef.current = false;
      setSendingId(null);
    }
  }

  async function sendSegmentMessage(conversationId, concept, imageInfo) {
    if (!imageInfo) return;
    if (voiceHandleRef.current || voiceBusyRef.current) stopVoiceCall();
    if (sendingRef.current) return;
    sendingRef.current = true;
    pushMessage(conversationId, {
      id: uid(),
      role: "user",
      kind: "text",
      text: concept,
      entryId: null,
      imagePreview: imageInfo.previewUrl || null,
      imageMime: imageInfo.mime || null,
    });
    setSendingId(conversationId);
    try {
      const segmented = await segmentImage(concept, { mime: imageInfo.mime, data: imageInfo.data });
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "segment",
        text: "",
        entryId: null,
        imagePreview: imageInfo.previewUrl || null,
        imageMime: imageInfo.mime || null,
        // Concept persisté en texte ; géométrie et aperçu en mémoire uniquement.
        segmentPrompt: concept,
        segmentImage: segmented.image,
        segmentMatches: segmented.matches,
        segmentTruncated: segmented.truncated,
      });
    } catch (err) {
      pushMessage(conversationId, {
        id: uid(),
        role: "assistant",
        kind: "error",
        text: err instanceof Error ? err.message : "An unexpected error occurred.",
        entryId: null,
        retryText: concept,
        retryKind: "segment",
        retryImage: imageInfo,
      });
    } finally {
      sendingRef.current = false;
      setSendingId(null);
    }
  }

  // Ré-édition d'un résultat : relit les octets mémoire via son URL blob
  // et les fournit au Composer comme une sélection (jamais persistée).
  // Relit les octets d'une image depuis son URL (blob: mémoire ou media
  // persisté) vers { blob, mime, data }. Lève si expiré ou illisible.
  async function readImageBytes(sourceUrl, mimeHint) {
    const blob = await (await fetch(sourceUrl)).blob();
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read the file."));
      reader.readAsDataURL(blob);
    });
    if (typeof dataUrl !== "string" || !dataUrl.includes(",")) {
      throw new Error("Could not read the file.");
    }
    return {
      blob,
      mime: mimeHint || blob.type || "image/png",
      data: dataUrl.slice(dataUrl.indexOf(",") + 1),
    };
  }

  async function requestImageEdit(conversationId, messageId) {
    const conversation = conversationsRef.current.find((c) => c.id === conversationId);
    const source = conversation?.messages.find((m) => m.id === messageId);
    const sourceUrl = source?.imagePreview || (source?.mediaId ? mediaUrl(source.mediaId) : null);
    if (!sourceUrl || sendingRef.current) return;
    try {
      const { blob, mime, data } = await readImageBytes(sourceUrl, source.imageMime || source.mediaMime);
      setImageSeed({
        key: uid(),
        image: {
          mime,
          data,
          previewUrl: URL.createObjectURL(blob),
          name: "Image ReFlow",
        },
      });
    } catch {
      // Aperçu expiré ou illisible : pas de graine, l'utilisateur rejoint une image.
    }
  }

  function retryMessage(conversationId, messageId) {
    const conversation = conversations.find((c) => c.id === conversationId);
    const failed = conversation?.messages.find((m) => m.id === messageId);
    if ((!failed?.retryText && !failed?.retryImage) || sendingId) return;
    setConversations((prev) =>
      prev.map((c) =>
        c.id === conversationId
          ? { ...c, messages: c.messages.filter((m) => m.id !== messageId) }
          : c
      )
    );
    if (failed.retryKind === "image") {
      sendImageMessage(conversationId, failed.retryText || "");
    } else if (failed.retryKind === "edit" && failed.retryImage) {
      sendEditImageMessage(conversationId, failed.retryText || "", failed.retryImage);
    } else if (failed.retryKind === "segment" && failed.retryImage) {
      sendSegmentMessage(conversationId, failed.retryText || "", failed.retryImage);
    } else {
      sendMessage(conversationId, failed.retryText || "", failed.retryImage || null);
    }
  }

  // Reconstruit les octets d'une image mémoire (blob: vivant) pour une
  // régénération. Retourne null si expiré, sans jamais lever.
  async function liveImageInfo(message) {
    if (!message?.imagePreview) return null;
    try {
      const { mime, data } = await readImageBytes(message.imagePreview, message.imageMime);
      return { mime, data, previewUrl: message.imagePreview, name: "Image ReFlow" };
    } catch {
      return null;
    }
  }

  // Régénère la réponse immédiate d'un message édité via la logique
  // d'envoi existante (mêmes appels API). Ne touche ni aux messages
  // suivants, ni aux plans (nouvelle entrée), ni aux exécutions.
  async function produceRegeneratedReply(conversation, job) {
    const { replyKind, text, before, oldReply, userMessage, forcedSource } = job;
    if (replyKind === "plan" || (replyKind === null && conversation.mode !== "chat")) {
      const entry = await generateAndStore(text);
      speakPlan(entry);
      return { kind: "plan", text: "", entryId: entry.id };
    }
    if (replyKind === "image") {
      // Une image source vivante signifie un flux d'édition ; sinon le
      // texte régénère une image neuve. Les deux sont persistés (media).
      const source = forcedSource || (await liveImageInfo(userMessage));
      const result = source
        ? await editImage(text, { mime: source.mime, data: source.data })
        : await generateImage(text);
      return {
        kind: "image",
        text: "",
        entryId: null,
        imagePrompt: text,
        imagePreview: blobUrlFromBase64(result.mime, result.data),
        imageMime: result.mime,
        mediaId: result.mediaId || null,
        mediaMime: result.mediaId ? result.mime : "",
      };
    }
    if (replyKind === "segment") {
      const source =
        forcedSource || (await liveImageInfo(oldReply)) || (await liveImageInfo(userMessage));
      if (!source) throw new Error("Source image expired: send it again to segment.");
      const segmented = await segmentImage(text, { mime: source.mime, data: source.data });
      return {
        kind: "segment",
        text: "",
        entryId: null,
        imagePreview: source.previewUrl,
        imageMime: source.mime,
        segmentPrompt: text,
        segmentImage: segmented.image,
        segmentMatches: segmented.matches,
        segmentTruncated: segmented.truncated,
      };
    }
    if (replyKind === "error") {
      const kind = oldReply?.retryKind;
      if (kind === "image") {
        return produceRegeneratedReply(conversation, { ...job, replyKind: "image" });
      }
      if (kind === "edit" || kind === "segment") {
        const source = oldReply?.retryImage || (await liveImageInfo(userMessage));
        if (!source) throw new Error("Source image expired: send it again.");
        return produceRegeneratedReply(conversation, {
          ...job,
          replyKind: kind === "segment" ? "segment" : "image",
          forcedSource: source,
        });
      }
      return produceRegeneratedReply(conversation, {
        ...job,
        replyKind: conversation.mode === "chat" ? "text" : "plan",
      });
    }
    // Texte (ou sans réponse en mode discussion) : historique tronqué au
    // message édité, les messages suivants ne fuient jamais dans le contexte.
    const history = before
      .filter((m) => m.kind !== "error" && typeof m.text === "string" && m.text.trim())
      .map((m) => ({
        role: m.role === "assistant" ? "assistant" : "user",
        text: m.text.trim().slice(0, MAX_CHAT_TEXT_LEN),
      }));
    const attached = await liveImageInfo(userMessage);
    history.push({
      role: "user",
      text: text.slice(0, MAX_CHAT_TEXT_LEN),
      ...(attached ? { image: { mime: attached.mime, data: attached.data } } : {}),
    });
    const reply = await sendChat(history.slice(-12));
    return { kind: "text", text: reply, entryId: null };
  }

  // Édition en place : le texte est remplacé (même id, précédent conservé
  // dans previousText) puis seule la réponse immédiate est régénérée.
  async function resendEditedMessage(conversationId, messageId, newText) {
    if (voiceHandleRef.current || voiceBusyRef.current) stopVoiceCall();
    if (sendingRef.current) return;
    const conversation = conversations.find((c) => c.id === conversationId);
    if (!conversation) return;
    const edit = applyTextEdit(conversation.messages, messageId, newText);
    if (!edit) return;
    const text = newText.trim();
    const before = historyBefore(conversation.messages, edit.userIndex);
    const oldReply = edit.replyIndex >= 0 ? conversation.messages[edit.replyIndex] : null;
    const userMessage = conversation.messages[edit.userIndex];
    sendingRef.current = true;
    setConversations((prev) =>
      prev.map((c) => (c.id === conversationId ? { ...c, messages: edit.messages } : c))
    );
    setSendingId(conversationId);
    try {
      const fresh = await produceRegeneratedReply(conversation, {
        ...edit,
        text,
        before,
        oldReply,
        userMessage,
      });
      replaceAssistantReply(conversationId, edit, fresh);
    } catch (err) {
      const retryImage =
        oldReply?.retryImage ||
        (await liveImageInfo(oldReply)) ||
        (await liveImageInfo(userMessage)) ||
        null;
      replaceAssistantReply(conversationId, edit, {
        kind: "error",
        text: err instanceof Error ? err.message : "An unexpected error occurred.",
        entryId: null,
        retryText: text,
        retryImage,
        retryKind:
          oldReply?.retryKind ||
          (edit.replyKind === "image" ? "image" : edit.replyKind === "segment" ? "segment" : undefined),
      });
    } finally {
      sendingRef.current = false;
      setSendingId(null);
    }
  }

  // Remplace la réponse immédiate en place (même id) ou l'insère après le
  // message édité quand il n'y en avait pas. Le reste du fil est inchangé.
  function replaceAssistantReply(conversationId, edit, fresh) {
    setConversations((prev) => {
      const target = prev.find((c) => c.id === conversationId);
      if (!target) return prev;
      const messages = replaceReplyAt(target.messages, edit, fresh, uid());
      const next = prev.map((c) => (c.id === conversationId ? { ...c, messages } : c));
      return touchConversation(next, conversationId, messages);
    });
  }

  function setConversationMode(conversationId, mode) {
    if (mode !== "plan" && mode !== "chat") return;
    setConversations((prev) => {
      const target = prev.find((c) => c.id === conversationId);
      if (!target || target.mode === mode) return prev;
      const next = prev.map((conversation) =>
        conversation.id === conversationId ? { ...conversation, mode } : conversation
      );
      return touchConversation(next, conversationId, target.messages);
    });
  }

  // Miroir de la sélection du Composer : appelée à chaque sélection,
  // retrait, envoi texte ou démontage. Référence seule, sans rendu.
  function handleComposerImageChange(imageInfo) {
    pendingImageRef.current = imageInfo;
  }

  // Miroir de la bascule Génération du Composer.
  function handleGenerateModeChange(value) {
    generateModeRef.current = value === true;
  }

  // Consommation one-shot du mode Génération par la voix : une seule
  // génération, puis réarmement (le Composer est décoché via generateEpoch).
  // Hors Discussion, ne consomme jamais.
  function consumeImageMode() {
    const conversation = conversationsRef.current.find(
      (c) => c.id === voiceConversationIdRef.current
    );
    if (conversation?.mode !== "chat" || !generateModeRef.current) return false;
    generateModeRef.current = false;
    setGenerateEpoch((epoch) => epoch + 1);
    return true;
  }

  // Récupération one-shot pour la voix continue : lit ET efface l'image,
  // qui ne part donc qu'avec le premier tour traité. En mode Plan (où le
  // sélecteur est masqué), ne consomme jamais : voix strictement textuelle.
  function getVoiceImage() {
    const conversation = conversationsRef.current.find(
      (c) => c.id === voiceConversationIdRef.current
    );
    if (conversation?.mode !== "chat") return null;
    const pending = pendingImageRef.current;
    pendingImageRef.current = null;
    if (pending) setImageEpoch((epoch) => epoch + 1);
    return pending;
  }

  async function startVoiceCall() {
    if (voiceBusyRef.current || voiceHandleRef.current) return;
    if (!activeConversationId) return;
    voiceBusyRef.current = true;
    // Capture le jeton de ce démarrage : seul ce jeton pourra valider
    // le handle obtenu. Un nouveau démarrage est impossible tant que
    // voiceBusyRef est levé, donc un jeton périmé ne peut pas appartenir
    // à une session valide plus récente.
    const startToken = voiceStartTokenRef.current + 1;
    voiceStartTokenRef.current = startToken;
    setVoiceStatus("starting");
    setVoiceError("");
    voiceConversationIdRef.current = activeConversationId;
    try {
      const handle = await startVoiceSession({
        getImage: getVoiceImage,
        consumeImageMode,
        getHistory: () => {
          const conversation = conversationsRef.current.find(
            (c) => c.id === voiceConversationIdRef.current
          );
          const messages = conversation ? conversation.messages : [];
          return messages
            .filter((m) => m.kind !== "error" && typeof m.text === "string" && m.text.trim())
            .map((m) => ({
              role: m.role === "assistant" ? "assistant" : "user",
              text: m.text.trim().slice(0, MAX_CHAT_TEXT_LEN),
            }));
        },
        onUserTranscript: (turn, text, previewUrl = null) => {
          pushMessage(voiceConversationIdRef.current, {
            id: `${uid()}-v${turn}`,
            role: "user",
            kind: "text",
            text,
            entryId: null,
            // Miniature du tour vocal avec image (mécanisme existant,
            // dépouillé avant persistance comme pour les envois texte).
            imagePreview: previewUrl,
            imageMime: null,
          });
        },
        onAssistantReply: (turn, text) => {
          pushMessage(voiceConversationIdRef.current, {
            id: `${uid()}-v${turn}-r`,
            role: "assistant",
            kind: "text",
            text,
            entryId: null,
          });
        },
        onAssistantImage: (turn, prompt, generated) => {
          let previewUrl = null;
          try {
            previewUrl = blobUrlFromBase64(generated.mime, generated.data);
          } catch {
            previewUrl = null;
          }
          pushMessage(voiceConversationIdRef.current, {
            id: `${uid()}-v${turn}-r`,
            role: "assistant",
            kind: "image",
            text: "",
            entryId: null,
            imagePrompt: prompt,
            imagePreview: previewUrl,
            imageMime: generated?.mime || null,
            mediaId: generated?.mediaId || null,
            mediaMime: generated?.mediaId ? generated.mime : "",
          });
        },
        onState: (state) => {
          if (state === "stopped") {
            voiceHandleRef.current = null;
            setVoiceStatus("idle");
          }
        },
        onError: (error) => {
          setVoiceError(error instanceof Error ? error.message : "Voice conversation error.");
        },
      });
      // Garde anti-démarrage-fantôme : stopVoiceCall a pu invalider ce
      // démarrage pendant l'attente. Handle périmé = arrêt immédiat,
      // jamais conservé, jamais affiché.
      if (startToken !== voiceStartTokenRef.current) {
        try {
          await handle.stop();
        } catch {
          // Arrêt silencieux d'une session périmée.
        }
        return;
      }
      voiceHandleRef.current = handle;
      setVoiceStatus("active");
    } catch (error) {
      // Démarrage périmé (arrêt ou navigation pendant l'attente) :
      // aucune erreur tardive, l'interface est déjà remise à idle.
      if (startToken !== voiceStartTokenRef.current) return;
      setVoiceError(
        error instanceof Error ? error.message : "Cannot start the voice conversation."
      );
      setVoiceStatus("idle");
    } finally {
      voiceBusyRef.current = false;
    }
  }

  async function stopVoiceCall() {
    // Invalide immédiatement tout démarrage en cours, même sans handle
    // encore (attente asynchrone dans startVoiceCall).
    voiceStartTokenRef.current += 1;
    const handle = voiceHandleRef.current;
    setVoiceStatus("idle");
    setVoiceError("");
    try {
      if (handle) await handle.stop();
    } catch {
      // Arrêt silencieux.
    } finally {
      if (voiceHandleRef.current === handle) voiceHandleRef.current = null;
    }
  }

  function newChat() {
    stopVoiceCall();
    stopSpeech();
    const conversation = createConversation("");
    setConversations((prev) => [conversation, ...prev]);
    setActiveConversationId(conversation.id);
    setView("assistant");
    setMobileOpen(false);
  }

  async function startConversation(text, imageInfo = null) {
    const conversation = createConversation(text);
    setConversations((prev) => [conversation, ...prev]);
    setActiveConversationId(conversation.id);
    setView("assistant");
    await sendMessage(conversation.id, text, imageInfo);
  }

  function openConversation(conversationId) {
    if (conversationId !== activeConversationId) stopVoiceCall();
    stopSpeech();
    setActiveConversationId(conversationId);
    setView("assistant");
    setMobileOpen(false);
  }

  function deleteConversation(conversationId) {
    if (conversationId === activeConversationId) stopVoiceCall();
    stopSpeech();
    // Libère les aperçus d'image mémoire de la conversation supprimée.
    const target = conversations.find((c) => c.id === conversationId);
    target?.messages.forEach((message) => {
      if (message.imagePreview) URL.revokeObjectURL(message.imagePreview);
    });
    setConversations((prev) => prev.filter((c) => c.id !== conversationId));
    setActiveConversationId((current) => (current === conversationId ? null : current));
  }

  function toggleStep(planId, index) {
    setPlans((prev) =>
      prev.map((plan) =>
        plan.id !== planId
          ? plan
          : { ...plan, done: plan.done.map((checked, i) => (i === index ? !checked : checked)) }
      )
    );
  }

  // Suivi local d'une tâche structurée (done/skipped/nul) : ne touche pas au backend.
  function trackAgentTask(planId, taskId, next) {
    setAgentPlans((prev) =>
      prev.map((plan) => {
        if (plan.id !== planId) return plan;
        const localStatus = { ...(plan.localStatus || {}) };
        if (next === "done" || next === "skipped") localStatus[taskId] = next;
        else delete localStatus[taskId];
        return { ...plan, localStatus };
      })
    );
  }

  // ---- Phase 4: approval + controlled execution (state persists in reflow:execution:v1)
  function approveExecTask(planId, task, actionType, uiParams) {
    const params = buildActionParams(actionType, uiParams, task);
    if (actionType === "file.organize") {
      const ok = window.confirm(
        "This action will create or move files inside the ReFlow output folder. Continue?"
      );
      if (!ok) return;
    }
    setExecutionStore((prev) => approveTaskState(prev, planId, task, task.title, actionType, params));
  }

  function rejectExecTask(planId, task) {
    setExecutionStore((prev) => rejectTaskState(prev, planId, task.id, task.title));
  }

  function revokeExecApproval(planId, task) {
    setExecutionStore((prev) => revokeTaskState(prev, planId, task.id, task.title));
  }

  function cancelExecTask() {
    try {
      execAbortRef.current?.abort();
    } catch {
      /* ignore */
    }
  }

  async function executeExecTask(entry, task, actionType, uiParams) {
    if (!entry || runningExec) return;
    const params = buildActionParams(actionType, uiParams, task);
    const ready = readiness(executionStore, entry, task, actionType, params, { running: runningExec });
    if (!ready.ok) return;
    const approval = executionStore.approvals?.[execKey(entry.id, task.id)] || null;
    if (!approval) return;
    const key = execKey(entry.id, task.id);
    const idempotencyKey = uid();
    const controller = new AbortController();
    execAbortRef.current = controller;
    setRunningExec(key);
    setExecutionStore((prev) => startExecutionState(prev, entry.id, task, actionType, idempotencyKey));
    try {
      const res = await executeTask(
        {
          plan_id: entry.id,
          task: {
            id: task.id,
            title: task.title,
            detail: task.detail || "",
            depends_on: task.depends_on || [],
            success_criteria: task.success_criteria || [],
            needs_info: task.needs_info || [],
            status: task.status,
          },
          action: { type: actionType, params },
          approval: { status: "approved", approved_at: approval.approved_at, snapshot: approval.snapshot },
          context_tasks: depStates(entry, executionStore, entry.id),
          idempotency_key: idempotencyKey,
        },
        { signal: controller.signal }
      );
      setExecutionStore((prev) =>
        res && res.status === "completed"
          ? completeExecutionState(prev, entry.id, task.id, task.title, res)
          : failExecutionState(prev, entry.id, task.id, task.title, res?.error || "Execution failed.")
      );
    } catch (err) {
      const cancelled = err && (err.name === "AbortError" || err.cancelled === true);
      setExecutionStore((prev) =>
        cancelled
          ? cancelExecutionState(prev, entry.id, task.id, task.title)
          : failExecutionState(prev, entry.id, task.id, task.title, err?.message || "Execution failed.")
      );
    } finally {
      if (execAbortRef.current === controller) execAbortRef.current = null;
      setRunningExec(null);
    }
  }

  function clearExecHistory() {
    const ok = window.confirm("Clear the execution history and approval records? This cannot be undone.");
    if (!ok) return;
    setExecutionStore({ version: 1, approvals: {}, rejections: {}, executions: {}, history: [] });
  }

  const execution = {
    store: executionStore,
    running: runningExec,
    actions: execActions,
    approve: approveExecTask,
    reject: rejectExecTask,
    revoke: revokeExecApproval,
    execute: executeExecTask,
    cancel: cancelExecTask,
  };

  // ---- Document Intelligence workspace (metadata + threads in reflow:documents:v1)
  async function uploadDoc(payload) {
    if (docBusy) throw new Error("Another document task is running.");
    setDocBusy({ kind: "upload" });
    try {
      const info = await uploadDocument(payload);
      setDocStore((prev) => rememberDoc(prev, info));
      return info;
    } finally {
      setDocBusy(null);
    }
  }

  async function runDocOperation(operation, { style = "simple", language = "en" } = {}) {
    const doc = currentDoc(docStore);
    if (!doc) throw new Error("No document selected.");
    if (docBusy) throw new Error("Another document task is running.");
    setDocBusy({ kind: "analyze", op: operation });
    try {
      const res = await analyzeDocument(doc.doc_id, { operation, style, language });
      setDocStore((prev) => putResult(prev, doc.doc_id, operation, res));
      return res;
    } finally {
      setDocBusy(null);
    }
  }

  async function askDocQuestion(question, { language = "en" } = {}) {
    const doc = currentDoc(docStore);
    if (!doc) throw new Error("No document selected.");
    if (docBusy) throw new Error("Another document task is running.");
    setDocBusy({ kind: "analyze", op: "ask" });
    try {
      const res = await analyzeDocument(doc.doc_id, { operation: "ask", question, language });
      setDocStore((prev) =>
        pushQA(putResult(prev, doc.doc_id, "ask", res), doc.doc_id, {
          q: question,
          a: res.result,
          refs: res.refs || [],
          ts: Date.now(),
        })
      );
      return res;
    } finally {
      setDocBusy(null);
    }
  }

  // Action Mode bridge: proposes a structured plan from extracted items via
  // the existing flow (validations, dependencies, human approval). Never executes.
  async function createPlanFromDoc() {
    const doc = currentDoc(docStore);
    if (!doc) throw new Error("No document selected.");
    const scoped = docStore.results?.[doc.doc_id] || {};
    const source =
      scoped.action_items?.result || scoped.extract?.result || scoped.summarize?.result || "";
    if (!source.trim()) {
      throw new Error("Run Action Items or Extract first to propose a plan.");
    }
    if (docBusy) throw new Error("Another document task is running.");
    setDocBusy({ kind: "plan" });
    try {
      const entry = await generateAndStoreAgentPlan(
        `Based on the document "${doc.filename}":\n${source.slice(0, 1800)}`
      );
      setView("agent-plans");
      return entry;
    } finally {
      setDocBusy(null);
    }
  }

  const docWorkspace = {
    store: docStore,
    busy: docBusy,
    upload: uploadDoc,
    analyze: runDocOperation,
    ask: askDocQuestion,
    select: (id) => setDocStore((prev) => selectDoc(prev, id)),
    remove: (id) => setDocStore((prev) => forgetDoc(prev, id)),
    createPlan: createPlanFromDoc,
  };

  function deletePlan(planId) {
    setPlans((prev) => prev.filter((plan) => plan.id !== planId));
    setActivePlanId((current) => (current === planId ? null : current));
  }

  function deleteAgentPlan(planId) {
    setAgentPlans((prev) => prev.filter((plan) => plan.id !== planId));
    setActiveAgentPlanId((current) => (current === planId ? null : current));
  }

  function openPlan(planId) {
    setActivePlanId(planId);
    setView("plans");
    setMobileOpen(false);
  }

  function navigate(next) {
    if (next !== view) {
      stopVoiceCall();
      stopSpeech();
    }
    setView(next);
  }

  const sending = sendingId !== null;

  return (
    <div className="app">
      <Sidebar
        view={view}
        collapsed={sidebarCollapsed}
        mobileOpen={mobileOpen}
        conversations={conversations}
        plansCount={plans.length}
        agentPlansCount={agentPlans.length}
        activeConversationId={activeConversationId}
        onNavigate={navigate}
        onNewChat={newChat}
        onOpenConversation={openConversation}
        onToggleCollapse={() => setSidebarCollapsed((value) => !value)}
        onCloseMobile={() => setMobileOpen(false)}
      />
      {mobileOpen && (
        <div className="backdrop" aria-hidden="true" onClick={() => setMobileOpen(false)} />
      )}
      <div className="content">
        <header className="topbar">
          <button
            type="button"
            className="icon-button ghost"
            onClick={() => setMobileOpen(true)}
            aria-label="Open the menu"
            title="Menu"
          >
            <PanelIcon />
          </button>
          <p className="topbar-title">{VIEW_TITLES[view] || "ReFlow"}</p>
          <button type="button" className="primary small new-chat-top" onClick={newChat}>
            <PlusIcon />
            <span>New Chat</span>
          </button>
        </header>
        <main className="main">
          <div className="view-wrap" key={view}>
            {view === "dashboard" && (
              <Dashboard onStartConversation={startConversation} sending={sending} />
            )}
            {view === "assistant" && (
              <>
                {activeConversation && (
                  <VoiceCallButton
                    status={voiceStatus}
                    error={voiceError}
                    disabled={sending}
                    onStart={startVoiceCall}
                    onStop={stopVoiceCall}
                  />
                )}
                <Assistant
                  conversation={activeConversation}
                  plans={plans}
                  sending={sendingId === activeConversationId}
                  mode={activeConversation?.mode === "chat" ? "chat" : "plan"}
                  onSend={sendMessage}
                  onRetry={retryMessage}
                  onEditMessage={resendEditedMessage}
                  onToggleStep={toggleStep}
                  onNewChat={newChat}
                  onModeChange={setConversationMode}
                  onImageChange={handleComposerImageChange}
                  imageEpoch={imageEpoch}
                  onGenerateImage={sendImageMessage}
                  onGenerateModeChange={handleGenerateModeChange}
                  generateEpoch={generateEpoch}
                  onEditImage={sendEditImageMessage}
                  allowEditImage={activeConversation?.mode === "chat"}
                  imageSeed={imageSeed}
                  onRequestEdit={requestImageEdit}
                  onSegmentImage={sendSegmentMessage}
                  allowSegmentImage={activeConversation?.mode === "chat"}
                />
              </>
            )}
            {view === "conversations" && (
              <Conversations
                conversations={conversations}
                onOpen={openConversation}
                onNew={newChat}
                onDelete={deleteConversation}
              />
            )}
            {view === "documents" && <Documents documents={docWorkspace} />}
            {view === "plans" && (
              <Plans
                plans={plans}
                activeId={activePlanId}
                onSelect={setActivePlanId}
                onToggleStep={toggleStep}
                onDelete={deletePlan}
              />
            )}
            {view === "agent-plans" && (
              <AgentPlans
                agentPlans={agentPlans}
                activeId={activeAgentPlanId}
                onSelect={setActiveAgentPlanId}
                onTrackTask={trackAgentTask}
                onDelete={deleteAgentPlan}
                onGenerate={generateAndStoreAgentPlan}
                execution={execution}
              />
            )}
            {view === "history" && (
              <ExecutionHistory store={executionStore} agentPlans={agentPlans} onClearHistory={clearExecHistory} />
            )}
            {view === "search" && (
              <Search
                conversations={conversations}
                plans={plans}
                onOpenConversation={openConversation}
                onOpenPlan={openPlan}
              />
            )}
            <footer className="footer">
              <p>ReFlow — plan suggestion to validate; no action runs automatically.</p>
            </footer>
          </div>
        </main>
      </div>
    </div>
  );
}
