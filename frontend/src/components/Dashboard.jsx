import { useState } from "react";
import Composer from "./Composer.jsx";

const SUGGESTIONS = [
  {
    label: "Organize a project",
    prompt:
      "Organize my website redesign project into phases, with the major steps and their dependencies.",
  },
  {
    label: "Create a schedule",
    prompt: "Prepare a 7-day revision schedule for my math and physics exams.",
  },
  {
    label: "Compare solutions",
    prompt:
      "Compare React, Vue, and Angular to choose a frontend framework: strengths, weaknesses, and a recommendation.",
  },
  {
    label: "Summarize a document",
    prompt: "Summarize the following text as key points: ",
  },
  {
    label: "Organize my tasks",
    prompt:
      "Organize my day's tasks: answer emails, prepare the client meeting, review the report, and exercise."
  },
  {
    label: "Solve a problem",
    prompt:
      "Help me solve this problem: my app is slow to start, where should I begin diagnosing?",
  },
];

let seedUid = 0;

export default function Dashboard({ onStartConversation, sending }) {
  const [seed, setSeed] = useState(null);

  function applySuggestion(suggestion) {
    seedUid += 1;
    setSeed({ key: seedUid, text: suggestion.prompt });
  }

  return (
    <div className="view home">
      <header className="hero-center">
        <svg
          className="hero-logo"
          viewBox="0 0 48 48"
          width="56"
          height="56"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M6 15h36" />
          <path d="M6 24h23" />
          <path d="M6 33h36" />
        </svg>
        <h2>What are we organizing today?</h2>
        <p className="muted">
          ReFlow turns your messy requests into concrete actions: plans, schedules,
          comparisons and procedures, validated before anything runs.
        </p>
      </header>

      <section className="card" aria-label="Input area">
        <Composer
          id="home-composer"
          placeholder="Describe your request, even roughly…"
          sending={sending}
          sendingLabel="Starting…"
          onSend={onStartConversation}
          seed={seed}
        />
      </section>

      <section aria-label="Quick suggestions">
        <ul className="suggestion-grid">
          {SUGGESTIONS.map((suggestion) => (
            <li key={suggestion.label}>
              <button
                type="button"
                className="suggestion-card"
                disabled={sending}
                onClick={() => applySuggestion(suggestion)}
              >
                <span className="suggestion-label">{suggestion.label}</span>
                <span className="suggestion-hint">Fill in the input box</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
