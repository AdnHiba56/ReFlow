import BlockRenderer, { hasRenderableContent } from "./blocks/BlockRenderer.jsx";
import TaskExecutionPanel from "./TaskExecutionPanel.jsx";
import {
  PLAN_STATUS_LABELS,
  TASK_STATUS_LABELS,
  agentProgressOf,
  effectiveStatus,
} from "../lib/agentPlans.js";
import { getExecution } from "../lib/execution.js";

const EXEC_CHIP = {
  completed: { className: "status-exec-completed", label: "Executed" },
  running: { className: "status-exec-running", label: "Running" },
  failed: { className: "status-exec-failed", label: "Failed" },
};

// Carte de plan structuré : objectif, tâches, dépendances, critères,
// infos manquantes. Le suivi local ne met pas à jour le backend.
export default function AgentPlanCard({ entry, onTrackTask, showRequest = true, execution = null }) {
  const progress = agentProgressOf(entry);
  const titles = {};
  for (const task of entry.tasks) titles[task.id] = task.title;
  const finishedCount = entry.tasks.filter((task) =>
    ["done", "skipped"].includes(effectiveStatus(task, entry))
  ).length;
  const blocks = Array.isArray(entry.blocks)
    ? entry.blocks.filter((block) => block && hasRenderableContent(block.type, block.content))
    : [];

  return (
    <article className="card plan" aria-labelledby={`agent-plan-${entry.id}`}>
      {showRequest && entry.request && (
        <blockquote className="plan-request">
          <span className="plan-request-label">Your request</span>
          {entry.request}
        </blockquote>
      )}
      <p className="plan-kicker">Structured plan — suggestion to validate, nothing is executed</p>
      <h3 id={`agent-plan-${entry.id}`}>{entry.goal}</h3>
      {entry.summary && <p className="muted">{entry.summary}</p>}
      <p>
        <span className={`status-badge status-${entry.status}`}>
          {PLAN_STATUS_LABELS[entry.status] || entry.status}
        </span>
      </p>

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
        {finishedCount} task{finishedCount > 1 ? "s" : ""} done{" "}
        or skipped out of {entry.tasks.length} — local tracking only.
      </p>

      {entry.missing_information.length > 0 && (
        <div className="missing-banner" role="note" aria-label="Missing information">
          <h4>Missing information</h4>
          <ul>
            {entry.missing_information.map((info) => (
              <li key={info.id}>
                <span className="task-id">{info.id}</span> {info.question}
                {info.blocks_task_ids.length > 0 && (
                  <span className="muted"> — blocking: {info.blocks_task_ids.join(", ")}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ol className="task-list">
        {entry.tasks.map((task) => {
          const status = effectiveStatus(task, entry);
          const execRecord = execution ? getExecution(execution.store, entry.id, task.id) : null;
          const execChip = EXEC_CHIP[execRecord?.status] || null;
          return (
            <li key={task.id} className="task-card">
              <div className="task-head">
                <span className="task-id" title={task.title}>
                  {task.id}
                </span>
                <span className="task-title">{task.title}</span>
                <button
                  type="button"
                  className={`status-badge status-${status}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onTrackTask(entry.id, task.id, status === "done" ? null : "done");
                  }}
                  aria-pressed={status === "done"}
                  aria-label={
                    status === "done"
                      ? `Restore ${task.id} to its original status`
                      : `Mark ${task.id} as done`
                  }
                  title={status === "done" ? "Restore original status" : "Mark as done"}
                >
                  {TASK_STATUS_LABELS[status] || status}
                </button>
                {execChip && (
                  <span className={`status-badge ${execChip.className}`} title="Real execution record">
                    {execChip.label}
                  </span>
                )}
              </div>
              {task.detail && <p className="task-detail">{task.detail}</p>}
              <p className="task-deps">
                {task.depends_on.length > 0 ? (
                  <>
                    After:{" "}
                    {task.depends_on.map((dep, index) => (
                      <span key={`${dep}-${index}`}>
                        {index > 0 && ", "}
                        <span className="dep-chip" title={titles[dep] || dep}>
                          {dep}
                        </span>
                      </span>
                    ))}
                  </>
                ) : (
                  <span className="muted">No dependencies — can start right away.</span>
                )}
                {task.needs_info.length > 0 && (
                  <span className="muted"> · Waiting on: {task.needs_info.join(", ")}</span>
                )}
              </p>
              {task.success_criteria.length > 0 && (
                <ul className="task-criteria" aria-label={`Success criteria for ${task.id}`}>
                  {task.success_criteria.map((criterion, index) => (
                    <li key={index}>{criterion}</li>
                  ))}
                </ul>
              )}
              <div className="task-track">
                <label>
                  <input
                    type="checkbox"
                    checked={status === "done"}
                    onChange={(event) =>
                      onTrackTask(entry.id, task.id, event.target.checked ? "done" : null)
                    }
                  />{" "}
                  Done (local tracking)
                </label>
                <button
                  type="button"
                  className="secondary small"
                  onClick={() => onTrackTask(entry.id, task.id, status === "skipped" ? null : "skipped")}
                >
                  {status === "skipped" ? "Unskip" : "Skip"}
                </button>
              </div>
              {execution && (
                <TaskExecutionPanel planId={entry.id} task={task} entry={entry} execution={execution} />
              )}
            </li>
          );
        })}
      </ol>

      {blocks.length > 0 && (
        <div className="blocks">
          {blocks.map((block, index) => (
            <BlockRenderer key={index} block={block} />
          ))}
        </div>
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
