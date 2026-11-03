import { textArray } from "./blockHelpers.js";

function cellText(value) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

export default function TableBlock({ content }) {
  const headers = textArray(content.headers, 8);
  const rawRows = Array.isArray(content.rows) ? content.rows : [];
  const rows = rawRows
    .filter((row) => Array.isArray(row))
    .slice(0, 20)
    .map((row) => row.map(cellText));
  if (headers.length === 0 || rows.length === 0) return null;
  const width = headers.length;

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {headers.map((header, index) => (
              <th key={index} scope="col">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {headers.map((_, c) => (
                <td key={c}>{row[c] || ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
