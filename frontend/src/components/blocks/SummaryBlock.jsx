import { textArray, textOf } from "./blockHelpers.js";

export default function SummaryBlock({ content }) {
  const points = textArray(content.points, 30);
  const conclusion = textOf(content.conclusion, "");
  if (points.length === 0 && !conclusion) return null;
  return (
    <div>
      {points.length > 0 && (
        <ul className="summary-points">
          {points.map((point, index) => (
            <li key={index}>{point}</li>
          ))}
        </ul>
      )}
      {conclusion ? <p className="summary-conclusion">{conclusion}</p> : null}
    </div>
  );
}
