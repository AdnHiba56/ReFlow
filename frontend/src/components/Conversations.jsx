import { formatDate } from "../lib/plans.js";

export default function Conversations({ conversations, onOpen, onNew, onDelete }) {
  if (conversations.length === 0) {
    return (
      <div className="view">
        <header className="view-head">
          <h2>My conversations</h2>
          <p className="muted">Find your saved exchanges in this browser here.</p>
        </header>
        <section className="card">
          <div className="empty">
            <p className="empty-title">No conversations yet</p>
            <p className="muted">Start a new conversation to find it here.</p>
            <button type="button" className="primary" onClick={onNew}>
              New conversation
            </button>
          </div>
        </section>
      </div>
    );
  }

  function handleDelete(event, conversation) {
    event.stopPropagation();
    if (window.confirm(`Delete the conversation "${conversation.title}"?`)) {
      onDelete(conversation.id);
    }
  }

  return (
    <div className="view">
      <header className="view-head">
        <h2>My conversations</h2>
        <p className="muted">
          {conversations.length} conversation{conversations.length > 1 ? "s" : ""} saved locally.
        </p>
      </header>
      <section className="card">
        <ul className="recent-list">
          {conversations.map((conversation) => (
            <li key={conversation.id} className="conversation-row">
              <button
                type="button"
                className="recent-item grow"
                onClick={() => onOpen(conversation.id)}
              >
                <span className="recent-title">{conversation.title}</span>
                <span className="recent-meta">
                  {formatDate(conversation.updatedAt)} · {conversation.messages.length} message
                  {conversation.messages.length > 1 ? "s" : ""}
                </span>
              </button>
              <button
                type="button"
                className="danger-ghost small"
                onClick={(event) => handleDelete(event, conversation)}
                aria-label={`Delete "${conversation.title}"`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
