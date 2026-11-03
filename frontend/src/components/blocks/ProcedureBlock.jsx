import { objectArray, textOf } from "./blockHelpers.js";

export default function ProcedureBlock({ content }) {
  const steps = objectArray(content.steps, 30);
  if (steps.length === 0) return null;
  return (
    <ol className="procedure">
      {steps.map((step, index) => (
        <li key={index}>
          <p className="procedure-title">{textOf(step.title, `Étape ${index + 1}`)}</p>
          {typeof step.detail === "string" && step.detail ? (
            <p className="procedure-detail">{step.detail}</p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
