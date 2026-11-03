import { objectArray, textArray, textOf } from "./blockHelpers.js";

export default function DecisionBlock({ content }) {
  const options = objectArray(content.options, 6);
  if (options.length === 0) return null;
  const recommendation = textOf(content.recommendation, "");

  return (
    <div>
      <ul className="decision-list">
        {options.map((option, index) => {
          const pros = textArray(option.pros, 10);
          const cons = textArray(option.cons, 10);
          return (
            <li key={index} className="decision-option">
              <p className="decision-name">{textOf(option.name, `Option ${index + 1}`)}</p>
              {pros.length > 0 && (
                <div className="decision-col">
                  <p className="decision-col-title pros">Pros</p>
                  <ul>
                    {pros.map((pro, i) => (
                      <li key={i}>{pro}</li>
                    ))}
                  </ul>
                </div>
              )}
              {cons.length > 0 && (
                <div className="decision-col">
                  <p className="decision-col-title cons">Cons</p>
                  <ul>
                    {cons.map((con, i) => (
                      <li key={i}>{con}</li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {recommendation ? (
        <p className="recommendation">
          <span className="recommendation-label">Suggested recommendation — validate before use</span>
          {recommendation}
        </p>
      ) : null}
    </div>
  );
}
