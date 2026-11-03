import { objectArray, textArray, textOf } from "./blockHelpers.js";

export default function ScheduleBlock({ content }) {
  const days = objectArray(content.days, 14);
  if (days.length === 0) return null;
  return (
    <ul className="schedule">
      {days.map((day, index) => {
        const tasks = textArray(day.tasks, 20);
        return (
          <li key={index} className="schedule-day">
            <p className="schedule-label">{textOf(day.label, `Jour ${index + 1}`)}</p>
            {tasks.length > 0 ? (
              <ul>
                {tasks.map((task, i) => (
                  <li key={i}>{task}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">No tasks listed.</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
