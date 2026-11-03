import { useState } from "react";
import { textArray } from "./blockHelpers.js";

export default function ChecklistBlock({ content }) {
  const items = textArray(content.items, 30);
  const [done, setDone] = useState(() => items.map(() => false));
  if (items.length === 0) return null;

  const checked = done.filter(Boolean).length;
  const progress = Math.round((checked / items.length) * 100);

  function toggle(index) {
    setDone((prev) => prev.map((value, i) => (i === index ? !value : value)));
  }

  return (
    <div>
      <div className="progress" role="progressbar" aria-valuenow={progress} aria-valuemin="0" aria-valuemax="100" aria-label="List progress">
        <div className="progress-bar" style={{ width: `${progress}%` }} />
      </div>
      <p className="progress-text">
        {checked} of {items.length} — local tracking only.
      </p>
      <ul className="checklist">
        {items.map((item, index) => (
          <li key={index} className={done[index] ? "done" : ""}>
            <label>
              <input type="checkbox" checked={done[index] === true} onChange={() => toggle(index)} />
              <span>{item}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
