import { formatDate } from "../lib/plans.js";

const EVENT_LABELS = {
  approved: "Approved",
  rejected: "Rejected",
  revoked: "Revoked",
  started: "Started",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  duplicate: "Duplicate ignored",
  interrupted: "Interrupted",
};

function shortTime(iso) {
  if (!iso) return "—";
  return formatDate(iso) || "—";
}

function planGoal(agentPlans, planId) {
  const plan = (agentPlans || []).find((entry) => entry.id === planId);
  return plan ? plan.goal : "Removed plan";
}

// Read-only view over the local execution store (reflow:execution:v1).
export default function ExecutionHistory({ store, agentPlans, onClearHistory }) {
  const approvals = store?.approvals || {};
  const executions = store?.executions || {};
  const history = Array.isArray(store?.history) ? store.history : [];

  const awaiting = Object.entries(approvals)
    .filter(([key]) => executions[key]?.status !== "completed")
    .map(([key, approval]) => {
      const separator = key.lastIndexOf(":");
      const planId = separator >= 0 ? key.slice(0, separator) : "";
      const taskId = separator >= 0 ? key.slice(separator + 1) : key;
      return { key, planId, taskId, approval };
    })
    .sort((a, b) => String(b.approval.approved_at).localeCompare(String(a.approval.approved_at)));

  const records = Object.entries(executions)
    .map(([key, record]) => {
      const separator = key.lastIndexOf(":");
      return {
        key,
        planId: separator >= 0 ? key.slice(0, separator) : "",
        taskId: separator >= 0 ? key.slice(separator + 1) : key,
        record,
      };
    })
    .sort((a, b) => String(b.record.requested_at).localeCompare(String(a.record.requested_at)));

  const events = [...history].reverse().slice(0, 50);

  return (
    <div className="view">
      <header className="view-head">
        <h2>Execution History</h2>
        <p className="muted">
          Approvals and executions stored locally in this browser for this demonstration.
        </p>
        {events.length > 0 && (
          <button type="button" className="secondary small" onClick={onClearHistory}>
            Clear history
          </button>
        )}
      </header>

      <section className="card" aria-label="Tasks awaiting execution">
        <h3>Awaiting execution</h3>
        {awaiting.length === 0 ? (
          <p className="muted">No approved task is waiting to run.</p>
        ) : (
          <ul className="history-list">
            {awaiting.map((item) => (
              <li key={item.key} className="history-item">
                <span className="history-title">
                  {item.taskId} · {item.approval.snapshot?.task?.title || "Untitled task"}
                </span>
                <span className="history-meta">
                  {item.approval.snapshot?.action?.type || "unknown action"} · approved{" "}
                  {shortTime(item.approval.approved_at)} · {planGoal(agentPlans, item.planId)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-label="Executions">
        <h3>Executions</h3>
        {records.length === 0 ? (
          <p className="muted">Nothing executed yet. Approve a task, then run it.</p>
        ) : (
          <ul className="history-list">
            {records.map((item) => (
              <li key={item.key} className="history-item">
                <span className="history-title">
                  {item.taskId} · {item.record.task_title || "Untitled task"}
                </span>
                <span className={`status-badge status-exec-${item.record.status || "none"}`}>
                  {EVENT_LABELS[item.record.status] || item.record.status || "none"}
                </span>
                <span className="history-meta">
                  {item.record.action_type} · requested {shortTime(item.record.requested_at)}
                  {item.record.finished_at ? ` · finished ${shortTime(item.record.finished_at)}` : ""}
                  {` · ${planGoal(agentPlans, item.planId)}`}
                </span>
                {item.record.result_summary && <p>{item.record.result_summary}</p>}
                {item.record.error && (
                  <p className="exec-error" role="alert">
                    {item.record.error}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-label="Recent events">
        <h3>Recent events</h3>
        {events.length === 0 ? (
          <p className="muted">No events recorded yet.</p>
        ) : (
          <ul className="history-list">
            {events.map((event, index) => (
              <li key={`${event.ts}-${index}`} className="history-item">
                <span className="history-title">
                  {EVENT_LABELS[event.event] || event.event} · {event.task_id}
                  {event.task_title ? ` · ${event.task_title}` : ""}
                </span>
                <span className="history-meta">
                  {shortTime(event.ts)}
                  {event.detail ? ` · ${event.detail}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
