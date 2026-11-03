import { ChatIcon, DocIcon, HistoryIcon, HomeIcon, PanelIcon, PlansIcon, PlusIcon, SearchIcon } from "./icons.jsx";

const ITEMS = [
  { id: "dashboard", label: "Home", Icon: HomeIcon },
  { id: "search", label: "Search", Icon: SearchIcon },
  { id: "conversations", label: "My conversations", Icon: ChatIcon },
  { id: "documents", label: "Documents", Icon: DocIcon },
  { id: "plans", label: "My plans", Icon: PlansIcon },
  { id: "agent-plans", label: "Structured plans", Icon: PlansIcon },
  { id: "history", label: "Execution History", Icon: HistoryIcon },
];

export default function Sidebar({
  view,
  collapsed,
  mobileOpen,
  conversations,
  plansCount,
  agentPlansCount = 0,
  activeConversationId,
  onNavigate,
  onNewChat,
  onOpenConversation,
  onToggleCollapse,
  onCloseMobile,
}) {
  const classes = ["sidebar"];
  if (collapsed) classes.push("collapsed");
  if (mobileOpen) classes.push("open");

  function go(id) {
    onNavigate(id);
    onCloseMobile();
  }

  return (
    <aside className={classes.join(" ")} aria-label="Sidebar">
      <div className="brand">
        <svg
          className="brand-logo"
          viewBox="0 0 32 32"
          width="32"
          height="32"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.6"
          strokeLinecap="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M4 10h24" />
          <path d="M4 16h15" />
          <path d="M4 22h24" />
        </svg>
        <div className="brand-text">
          <span className="brand-name">ReFlow</span>
          <span className="brand-sub">AI action plans</span>
        </div>
      </div>

      <button type="button" className="new-chat" onClick={() => { onNewChat(); onCloseMobile(); }}>
        <PlusIcon />
        <span>New conversation</span>
      </button>

      <nav aria-label="Main navigation">
        <ul>
          {ITEMS.map(({ id, label, Icon }) => (
            <li key={id}>
              <button
                type="button"
                className={view === id ? "nav-item active" : "nav-item"}
                aria-current={view === id ? "page" : undefined}
                title={label}
                onClick={() => go(id)}
              >
                <Icon />
                <span className="nav-label">{label}</span>
                {id === "plans" && plansCount > 0 && (
                  <span className="badge" aria-label={`${plansCount} plans`}>
                    {plansCount}
                  </span>
                )}
                {id === "agent-plans" && agentPlansCount > 0 && (
                  <span className="badge" aria-label={`${agentPlansCount} structured plans`}>
                    {agentPlansCount}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {conversations.length > 0 && (
        <div className="recents">
          <p className="recents-title">Recent conversations</p>
          <ul>
            {conversations.slice(0, 5).map((conversation) => (
              <li key={conversation.id}>
                <button
                  type="button"
                  className={
                    conversation.id === activeConversationId ? "recent-link active" : "recent-link"
                  }
                  onClick={() => {
                    onOpenConversation(conversation.id);
                    onCloseMobile();
                  }}
                >
                  {conversation.title}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="sidebar-foot">
        <button
          type="button"
          className="icon-button ghost collapse-btn"
          onClick={onToggleCollapse}
          aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
          title={collapsed ? "Expand" : "Collapse"}
        >
          <PanelIcon />
        </button>
        <p className="sidebar-note">Data is stored locally, in this browser.</p>
      </div>
    </aside>
  );
}
