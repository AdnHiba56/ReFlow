import PlanCard from "./PlanCard.jsx";
import { formatDate, progressOf } from "../lib/plans.js";

export default function Plans({ plans, activeId, onSelect, onToggleStep, onDelete }) {
  const active = plans.find((plan) => plan.id === activeId) || plans[0] || null;

  if (plans.length === 0) {
    return (
      <div className="view">
        <header className="view-head">
          <h2>My plans</h2>
          <p className="muted">Find the plans you generated in this browser here.</p>
        </header>
        <section className="card">
          <div className="empty">
            <p className="empty-title">No saved plans</p>
            <p className="muted">
              Generate a plan from Home or the Assistant: it will be kept here, locally.
            </p>
          </div>
        </section>
      </div>
    );
  }

  function handleDelete() {
    if (!active) return;
    if (window.confirm(`Remove the plan "${active.title}" from My plans?`)) {
      onDelete(active.id);
    }
  }

  return (
    <div className="view">
      <header className="view-head">
        <h2>My plans</h2>
        <p className="muted">
          {plans.length} plan{plans.length > 1 ? "s" : ""} saved{" "}
          locally in this browser.
        </p>
      </header>
      <div className="plans-layout">
        <section className="card plans-list" aria-label="Plans list">
          <ul>
            {plans.map((plan) => (
              <li key={plan.id}>
                <button
                  type="button"
                  className={active && plan.id === active.id ? "plan-item active" : "plan-item"}
                  aria-current={active && plan.id === active.id ? "true" : undefined}
                  onClick={() => onSelect(plan.id)}
                >
                  <span className="plan-item-title">{plan.title}</span>
                  <span className="plan-item-meta">
                    {formatDate(plan.createdAt)} · {progressOf(plan)}% tracked
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        {active && (
          <div className="plans-detail">
            <PlanCard entry={active} onToggleStep={onToggleStep} />
            <button type="button" className="danger-ghost" onClick={handleDelete}>
              Remove this plan
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
