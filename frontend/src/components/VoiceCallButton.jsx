// Bouton de conversation vocale : présentation seule, sans état propre.
// Le parent possède l'état (via startVoiceSession/stop) et pilote tout par props :
// - status : "idle" | "starting" | "active" (toute autre valeur retombe sur idle) ;
// - error : message affiché quand non vide, quel que soit le statut ;
// - disabled : verrouille le démarrage (ex. navigateur incompatible) ;
// - onStart / onStop : actions fournies par le parent.
import { MicIcon } from "./icons.jsx";

export default function VoiceCallButton({
  status = "idle",
  error = "",
  disabled = false,
  onStart,
  onStop,
}) {
  const active = status === "active";
  const starting = status === "starting";
  const canStart = typeof onStart === "function" && !disabled;
  const canStop = typeof onStop === "function";

  return (
    <div className="voice-call">
      {active ? (
        <div className="voice-call-row">
          <span className="voice-call-status" aria-live="polite">
            <span className="voice-dot" aria-hidden="true" />
            ReFlow is listening…
          </span>
          <button
            type="button"
            className="danger-ghost small"
            onClick={onStop}
            disabled={!canStop}
            aria-label="Stop the voice conversation"
          >
            Stop
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="primary"
          onClick={onStart}
          disabled={!canStart || starting}
          aria-label={
            starting
              ? "Voice conversation starting"
              : "Start the voice conversation"
          }
          aria-busy={starting || undefined}
        >
          <MicIcon />
          {starting ? "Starting…" : "Start the voice conversation"}
        </button>
      )}
      {error ? (
        <p className="voice-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
