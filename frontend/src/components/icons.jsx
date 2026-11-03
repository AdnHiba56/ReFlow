// Icônes SVG internes (aucune dépendance ajoutée).
function Base({ children }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function HomeIcon() {
  return (
    <Base>
      <path d="M4 11l8-7 8 7" />
      <path d="M6 9.5V20h12V9.5" />
      <path d="M10 20v-6h4v6" />
    </Base>
  );
}

export function ChatIcon() {
  return (
    <Base>
      <path d="M4 5h16v11H9l-5 4V5z" />
      <path d="M8 9.5h8M8 13h5" />
    </Base>
  );
}

export function PlansIcon() {
  return (
    <Base>
      <rect x="5" y="5" width="14" height="16" rx="2" />
      <path d="M9 5a3 3 0 0 1 6 0" />
      <path d="M9 11h6M9 15h6" />
    </Base>
  );
}

export function SendIcon() {
  return (
    <Base>
      <path d="M4 12L20 5l-7 15-2.5-7L4 12z" />
      <path d="M10.5 13L20 5" />
    </Base>
  );
}

export function MicIcon() {
  return (
    <Base>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0" />
      <path d="M12 17.5V21" />
    </Base>
  );
}

export function ImageIcon() {
  return (
    <Base>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M4.5 17.5l4.5-4.5 3 3 3-3 4.5 4.5" />
    </Base>
  );
}

export function SearchIcon() {
  return (
    <Base>
      <circle cx="11" cy="11" r="6" />
      <path d="M15.5 15.5L20 20" />
    </Base>
  );
}

export function PlusIcon() {
  return (
    <Base>
      <path d="M12 5v14M5 12h14" />
    </Base>
  );
}

export function PanelIcon() {
  return (
    <Base>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M9.5 5v14" />
    </Base>
  );
}

export function CameraIcon() {
  return (
    <Base>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <circle cx="12" cy="13.5" r="3.5" />
      <path d="M8.5 7l1.2-2.5h4.6L15.5 7" />
    </Base>
  );
}

export function PencilIcon() {
  return (
    <Base>
      <path d="M4 20l1-4.5L16.5 4l3.5 3.5L8.5 19 4 20z" />
      <path d="M14.5 6l3.5 3.5" />
    </Base>
  );
}

export function DocIcon() {
  return (
    <Base>
      <path d="M7 3h7l4 4v14H7V3z" />
      <path d="M14 3v4h4" />
      <path d="M10 12h5M10 16h5" />
    </Base>
  );
}

export function HistoryIcon() {
  return (
    <Base>
      <circle cx="12" cy="12" r="7.5" />
      <path d="M12 8.5V12l2.8 1.8" />
    </Base>
  );
}
