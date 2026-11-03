import { useEffect, useState } from "react";
import BlockRenderer, { hasRenderableContent } from "./blocks/BlockRenderer.jsx";
import {
  isSpeechPlaying,
  isSpeechSupported,
  onSpeechChange,
  speakPlan,
  speakingEntryId,
  stopSpeech,
} from "../lib/speech.js";
import { progressOf } from "../lib/plans.js";

export default function PlanCard({ entry, onToggleStep, showRequest = true }) {
  const [tracking, setTracking] = useState(false);
  const speechSupported = isSpeechSupported();
  const [reading, setReading] = useState(
    () => speechSupported && isSpeechPlaying() && speakingEntryId() === entry.id
  );

  useEffect(() => {
    if (!speechSupported) return undefined;
    const update = () => setReading(isSpeechPlaying() && speakingEntryId() === entry.id);
    update();
    return onSpeechChange(update);
  }, [speechSupported, entry.id]);
  const progress = progressOf(entry);
  const checkedCount = entry.done.filter(Boolean).length;
  const blocks = Array.isArray(entry.blocks)
    ? entry.blocks.filter((block) => block && hasRenderableContent(block.type, block.content))
    : [];

  return (
    <article className="card plan" aria-labelledby={`plan-${entry.id}`}>
      {showRequest && entry.request && (
        <blockquote className="plan-request">
          <span className="plan-request-label">Your request</span>
          {entry.request}
        </blockquote>
      )}
      <p className="plan-kicker">ReFlow suggestion — validate before use, nothing is executed</p>
      <h3 id={`plan-${entry.id}`}>{entry.title}</h3>
      {speechSupported && (
        <div className="speech-actions">
          {reading ? (
            <button type="button" className="secondary small" onClick={stopSpeech}>
              Stop playback
            </button>
          ) : (
            <button type="button" className="secondary small" onClick={() => speakPlan(entry)}>
              Listen
            </button>
          )}
        </div>
      )}

      {blocks.length > 0 ? (
        <div className="blocks">
          {blocks.map((block, index) => (
            <BlockRenderer key={index} block={block} />
          ))}
        </div>
      ) : tracking ? (
        <>
          <div
            className="progress"
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin="0"
            aria-valuemax="100"
            aria-label="Local tracking progress"
          >
            <div className="progress-bar" style={{ width: `${progress}%` }} />
          </div>
          <p className="progress-text">
            {checkedCount} step{checkedCount > 1 ? "s" : ""} tracked out of{" "}
            {entry.steps.length} — local tracking only.
          </p>
          <ol className="steps checkable">
            {entry.steps.map((step, index) => (
              <li key={index} className={entry.done[index] ? "done" : ""}>
                <label>
                  <input
                    type="checkbox"
                    checked={entry.done[index] === true}
                    onChange={() => onToggleStep(entry.id, index)}
                  />
                  <span>{step}</span>
                </label>
              </li>
            ))}
          </ol>
          <div className="fallback-actions">
            <button type="button" className="secondary small" onClick={() => setTracking(false)}>
              Back to the numbered list
            </button>
          </div>
        </>
      ) : (
        <>
          <ol className="procedure fallback-numbered">
            {entry.steps.map((step, index) => (
              <li key={index}>
                <p className="procedure-title">{step}</p>
              </li>
            ))}
          </ol>
          <div className="fallback-actions">
            <button type="button" className="secondary small" onClick={() => setTracking(true)}>
              Track these steps as a checklist
            </button>
          </div>
        </>
      )}

      {entry.questions.length > 0 && (
        <div className="questions">
          <h4>Clarifying questions</h4>
          <ul>
            {entry.questions.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}
