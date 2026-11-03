import { useState } from "react";
import { searchAll } from "../lib/conversations.js";
import { formatDate } from "../lib/plans.js";

export default function Search({ conversations, plans, onOpenConversation, onOpenPlan }) {
  const [query, setQuery] = useState("");
  const { conversations: foundConversations, plans: foundPlans } = searchAll(
    query,
    conversations,
    plans
  );
  const searched = query.trim().length >= 2;

  return (
    <div className="view">
      <header className="view-head">
        <h2>Search</h2>
        <p className="muted">Local search across your conversations and plans.</p>
      </header>
      <section className="card">
        <label className="field-label" htmlFor="search-input">
          Search
        </label>
        <input
          id="search-input"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="E.g.: moving, React, budget…"
          autoComplete="off"
        />
      </section>

      {!searched ? (
        <section className="card">
          <div className="empty">
            <p className="empty-title">Type at least 2 characters</p>
            <p className="muted">Search covers saved titles and contents.</p>
          </div>
        </section>
      ) : foundConversations.length === 0 && foundPlans.length === 0 ? (
        <section className="card">
          <div className="empty">
            <p className="empty-title">No results</p>
            <p className="muted">Try another keyword.</p>
          </div>
        </section>
      ) : (
        <>
          {foundConversations.length > 0 && (
            <section className="card" aria-label="Matching conversations">
              <h3>Conversations</h3>
              <ul className="recent-list">
                {foundConversations.map((conversation) => (
                  <li key={conversation.id}>
                    <button
                      type="button"
                      className="recent-item"
                      onClick={() => onOpenConversation(conversation.id)}
                    >
                      <span className="recent-title">{conversation.title}</span>
                      <span className="recent-meta">{formatDate(conversation.updatedAt)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {foundPlans.length > 0 && (
            <section className="card" aria-label="Matching plans">
              <h3>Plans</h3>
              <ul className="recent-list">
                {foundPlans.map((plan) => (
                  <li key={plan.id}>
                    <button
                      type="button"
                      className="recent-item"
                      onClick={() => onOpenPlan(plan.id)}
                    >
                      <span className="recent-title">{plan.title}</span>
                      <span className="recent-meta">
                        {formatDate(plan.createdAt)} · {plan.steps.length} steps
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
