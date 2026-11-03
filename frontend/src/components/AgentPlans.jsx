import { useState } from "react";
import AgentPlanCard from "./AgentPlanCard.jsx";
import { formatDate } from "../lib/plans.js";
import { PLAN_STATUS_LABELS, agentProgressOf } from "../lib/agentPlans.js";

const MAX_CHARS = 2000;

// Vue Plans structurés : génération via POST /plan/structured + liste
// des plans conservés localement. Aucune exécution de tâche ici.
export default function AgentPlans({
  agentPlans,
  activeId,
  onSelect,
  onTrackTask,
  onDelete,
  onGenerate,
  execution = null,
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const active = agentPlans.find((plan) => plan.id === activeId) || agentPlans[0] || null;

  async function submit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError("");
    try {
      await onGenerate(text);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur inattendue est survenue.");
    } finally {
      setSending(false);
    }
  }

  function handleDelete() {
    if (!active) return;
    if (window.confirm(`Remove the structured plan "${active.goal}"?`)) {
      onDelete(active.id);
    }
  }

  return (
    <div className="view">
      <header className="view-head">
        <h2>Structured plans</h2>
        <p className="muted">
          Describe your goal: ReFlow breaks it into ordered tasks with dependencies,
          success criteria and missing information.
        </p>
      </header>

      <section className="card" aria-label="Generate a structured plan">
        <form className="agent-form" onSubmit={submit}>
          <label className="field-label" htmlFor="agent-plan-request">
            Your goal
          </label>
          <textarea
            id="agent-plan-request"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="E.g. Organizing the launch of my portfolio site…"
            rows={3}
            maxLength={MAX_CHARS}
            disabled={sending}
          />
          <div className="composer-row">
            <span className="counter" aria-live="polite">
              {MAX_CHARS - draft.length} characters left
            </span>
            <span className="composer-buttons">
              <button type="submit" className="primary" disabled={sending || !draft.trim()}>
                {sending ? "Generating…" : "Generate plan"}
              </button>
            </span>
          </div>
          {sending && (
            <p className="muted" aria-live="polite">
              Analyzing your request and building the plan…
            </p>
          )}
          {error && (
            <p className="voice-error" role="alert">
              {error}
            </p>
          )}
        </form>
      </section>

      {agentPlans.length === 0 ? (
        <section className="card">
          <div className="empty">
            <p className="empty-title">No structured plans saved</p>
            <p className="muted">
              Generate your first plan above: it will be kept here, locally.
            </p>
          </div>
        </section>
      ) : (
        <div className="plans-layout">
          <section className="card plans-list" aria-label="Structured plans list">
            <ul>
              {agentPlans.map((plan) => (
                <li key={plan.id}>
                  <button
                    type="button"
                    className={active && plan.id === active.id ? "plan-item active" : "plan-item"}
                    aria-current={active && plan.id === active.id ? "true" : undefined}
                    onClick={() => onSelect(plan.id)}
                  >
                    <span className="plan-item-title">{plan.goal}</span>
                    <span className="plan-item-meta">
                      {formatDate(plan.createdAt)} · {PLAN_STATUS_LABELS[plan.status] || plan.status}{" "}
                      · {agentProgressOf(plan)}% tracked
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
          {active && (
            <div className="plans-detail">
              <AgentPlanCard entry={active} onTrackTask={onTrackTask} execution={execution} />
              <button type="button" className="danger-ghost" onClick={handleDelete}>
                Remove this plan
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
