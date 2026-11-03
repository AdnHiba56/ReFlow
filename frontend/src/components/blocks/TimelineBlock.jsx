import { objectArray, textOf } from "./blockHelpers.js";

export default function TimelineBlock({ content }) {
  const events = objectArray(content.events, 30);
  if (events.length === 0) return null;
  return (
    <ol className="timeline">
      {events.map((event, index) => (
        <li key={index}>
          <span className="timeline-label">{textOf(event.label, `Étape ${index + 1}`)}</span>
          {typeof event.detail === "string" && event.detail ? (
            <span className="timeline-detail">{event.detail}</span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
