// Lecture vocale des plans et des textes avec la synthèse native du navigateur.
// Aucune dépendance, aucun réseau : window.speechSynthesis uniquement.
// Ne lit que le texte utile (titre, étapes, questions), jamais les tableaux
// ni les blocs complexes. Les énoncés courts chaînés + une reprise prudente
// contournent la pause de Chrome sur les longues lectures.

const CHUNK_MAX = 220; // ≈ 15 s de parole : sous le seuil de pause de Chrome
const RESUME_EVERY_MS = 10000;

export function isSpeechSupported() {
  return (
    typeof window !== "undefined" &&
    !!window.speechSynthesis &&
    typeof SpeechSynthesisUtterance !== "undefined"
  );
}

function pickVoice(prefix) {
  // Lecture fraîche à chaque synthèse : si la liste est encore vide au
  // premier appel (chargement asynchrone du navigateur), les appels
  // suivants la trouveront chargée, sans écouteur voiceschanged.
  const voices = window.speechSynthesis.getVoices();
  if (!Array.isArray(voices)) return null;
  return (
    voices.find(
      (voice) =>
        typeof voice?.lang === "string" && voice.lang.toLowerCase().startsWith(prefix)
    ) || null
  );
}

// Listes volontairement prudentes : tout mot existant dans les deux langues
// (a, an, or, car, son, ton, pour, comment, on, ai, encore...) est exclu des
// deux listes, et les interjections ambiguës (ok, yes, no...) ne votent pas :
// en cas d'égalité ou d'absence d'indice, l'anglais gagne par défaut.
const FRENCH_WORDS = new Set(
  "le la les un une des du de et est sont suis ont avez avons que qui quoi dont ou je tu il ils elle elles nous vous leur leurs mon ma mes tes ses sa notre nos votre vos ce cet cette ces ceux celui ceci cela voici avec dans sur sous sans vers chez entre avant depuis pendant alors ainsi ensuite puis donc mais comme plus moins aussi autre autres chaque plusieurs tout tous toute toutes aucun aucune personne rien jamais toujours souvent beaucoup bien bon bonne jour jours fois exemple monsieur madame bonjour bonsoir salut merci oui non peux peut peuvent veux veut veulent vais vont dois doivent faut parle parles parler dis dit dire vois voit voir sais sait savoir viens vient venir prends prend prendre mets mettre trouve trouver reste rester demande demander utilise utiliser explique expliquer montre montrer donne donner faire fait avoir avait trop assez presque surtout plupart ne se y qu lorsque puisque quoique afin jusqu tandis toutefois cependant pourtant".split(
    " "
  )
);
const ENGLISH_WORDS = new Set(
  "i am my is are was were be been being have has had having do does did doing done don doesn didn isn aren wasn weren haven hasn hadn wouldn couldn shouldn won ain will would shall should can could may might must let re ve ll you your yours he him his she her hers it its we us our ours they them their theirs this that these those what when where which who whom whose why how with from for about into over after before between through during until while because though although however also just only very well here there out today tomorrow yesterday now then than too again once always never every each other another such same own many much more most some any all both few please thanks thank hello hi hey good great best better new first last little time times day days year years way ways thing things something nothing everything anything people work made make take come go goes get got see know think want need like love help use find give tell say said ask try call turn put set run read write speak talk listen look watch play learn understand remember believe feel seem become leave keep hold bring begin show hear meet pay build send receive agree".split(
    " "
  )
);
const FRENCH_ACCENTS = /[àâäéèêëîïôöùûüÿçœæ]/g;

// Détection déterministe français/anglais, sans dépendance. Les accents
// tranchent vite (poids double) ; sinon les mots-outils votent. Français
// seulement si strictement majoritaire : ambiguïté → anglais.
function detectLang(text) {
  if (typeof text !== "string" || !text.trim()) return "en";
  const lower = text.toLowerCase();
  let fr = 0;
  let en = 0;
  const accents = lower.match(FRENCH_ACCENTS);
  if (accents) fr += accents.length * 2;
  const words = lower.match(/[a-zàâäéèêëîïôöùûüÿçœæ]+/g) || [];
  for (const word of words) {
    if (FRENCH_WORDS.has(word)) fr += 1;
    if (ENGLISH_WORDS.has(word)) en += 1;
  }
  return fr > en ? "fr" : "en";
}

function splitLong(text, max = CHUNK_MAX) {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return [];
  if (clean.length <= max) return [clean];
  const sentences = clean.split(/(?<=[.!?…:;])\s+/);
  const parts = [];
  let current = "";
  const flush = () => {
    if (current) {
      parts.push(current);
      current = "";
    }
  };
  for (const sentence of sentences) {
    const grown = current ? `${current} ${sentence}` : sentence;
    if (grown.length <= max) {
      current = grown;
      continue;
    }
    flush();
    if (sentence.length <= max) {
      current = sentence;
      continue;
    }
    let rest = sentence;
    while (rest.length > max) {
      let cut = rest.lastIndexOf(" ", max);
      if (cut <= 0) cut = max;
      parts.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trim();
    }
    current = rest;
  }
  flush();
  return parts.length > 0 ? parts : [clean];
}

// Titre, étapes numérotées et questions : jamais les tableaux ni les blocs complexes.
export function planToSpeech(entry) {
  const parts = [];
  const title = typeof entry?.title === "string" ? entry.title.trim() : "";
  if (title) parts.push(title);
  const steps = Array.isArray(entry?.steps)
    ? entry.steps.filter((step) => typeof step === "string" && step.trim())
    : [];
  steps.forEach((step, index) => {
    parts.push(`Step ${index + 1}. ${step.trim()}`);
  });
  const questions = Array.isArray(entry?.questions)
    ? entry.questions.filter((question) => typeof question === "string" && question.trim())
    : [];
  if (questions.length > 0) {
    parts.push("Clarifying questions.");
    questions.forEach((question) => parts.push(question.trim()));
  }
  return parts.flatMap((part) => splitLong(part));
}

let current = null;
const listeners = new Set();

export function isSpeechPlaying() {
  return current !== null;
}

export function speakingEntryId() {
  return current ? current.id : null;
}

export function onSpeechChange(fn) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function emit() {
  const playing = current !== null;
  listeners.forEach((fn) => {
    try {
      fn(playing);
    } catch {
      // Un abonné défaillant ne doit pas casser la lecture.
    }
  });
}

function disarm(session) {
  if (session.watchdog) {
    clearInterval(session.watchdog);
    session.watchdog = null;
  }
}

export function stopSpeech() {
  if (!isSpeechSupported()) return;
  if (current) {
    current.stopped = true;
    disarm(current);
    current = null;
  }
  try {
    window.speechSynthesis.cancel();
  } catch {
    // cancel() indisponible : l'état local suffit.
  }
  emit();
}

function speakTexts(texts, entryId, lang = "en") {
  if (!isSpeechSupported()) return false;
  if (!Array.isArray(texts) || texts.length === 0) return false;
  // Annule toute lecture précédente : pas de chevauchement, pas de doublon.
  stopSpeech();
  const synth = window.speechSynthesis;
  // Voix adaptée à la langue ; repli : voix par défaut + langue phonétique.
  const voiceLang = lang === "en" ? "en" : "fr";
  const voice = pickVoice(voiceLang);
  const session = {
    id: entryId,
    stopped: false,
    watchdog: null,
  };
  current = session;
  let index = 0;

  function end() {
    disarm(session);
    if (current === session) {
      current = null;
      emit();
    }
  }

  function next() {
    if (session.stopped || current !== session) return;
    if (index >= texts.length) {
      end();
      return;
    }
    const utter = new SpeechSynthesisUtterance(texts[index]);
    utter.lang = voiceLang === "en" ? "en-US" : "fr-FR";
    if (voice) utter.voice = voice;
    utter.onend = () => {
      index += 1;
      next();
    };
    utter.onerror = () => {
      end();
    };
    synth.speak(utter);
  }

  // Reprise prudente : si Chrome met en pause, resume() relance (sans effet sinon).
  session.watchdog = setInterval(() => {
    if (session.stopped || current !== session) {
      disarm(session);
      return;
    }
    try {
      if (synth.speaking) synth.resume();
    } catch {
      // Reprise impossible : la lecture continue ou s'achève normalement.
    }
  }, RESUME_EVERY_MS);

  emit();
  next();
  return true;
}

export function speakPlan(entry) {
  if (!isSpeechSupported()) return false;
  const texts = planToSpeech(entry);
  if (texts.length === 0) return false;
  // Plans are read aloud in English by default: English voice and phonetics.
  return speakTexts(texts, entry && typeof entry.id === "string" ? entry.id : null, "en");
}

export function speakText(text) {
  if (typeof text !== "string") return false;
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return false;
  // Conversation bilingue : la langue est détectée une fois par réponse.
  return speakTexts(splitLong(clean), null, detectLang(clean));
}
