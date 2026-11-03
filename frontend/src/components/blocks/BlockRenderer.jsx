import ArticleBlock from "./ArticleBlock.jsx";
import ChecklistBlock from "./ChecklistBlock.jsx";
import TableBlock from "./TableBlock.jsx";
import TreeBlock from "./TreeBlock.jsx";
import TimelineBlock from "./TimelineBlock.jsx";
import DecisionBlock from "./DecisionBlock.jsx";
import ProcedureBlock from "./ProcedureBlock.jsx";
import ScheduleBlock from "./ScheduleBlock.jsx";
import SummaryBlock from "./SummaryBlock.jsx";
import { objectArray, textArray, textOf } from "./blockHelpers.js";

const RENDERERS = {
  article: ArticleBlock,
  checklist: ChecklistBlock,
  table: TableBlock,
  tree: TreeBlock,
  timeline: TimelineBlock,
  decision: DecisionBlock,
  procedure: ProcedureBlock,
  schedule: ScheduleBlock,
  summary: SummaryBlock,
};

export const BLOCK_TYPES = Object.keys(RENDERERS);

// Helpers partagés : voir ./blockHelpers.js (importés plus haut).

// Un bloc n'est conservé que s'il a un rendu réel : sinon, repli numéroté.
export function hasRenderableContent(type, content) {
  if (!RENDERERS[type] || !content || typeof content !== "object" || Array.isArray(content)) {
    return false;
  }
  switch (type) {
    case "article": {
      if (textOf(content.introduction) === "" || textOf(content.conclusion) === "") return false;
      const sections = objectArray(content.sections);
      return sections.some(
        (section) => textOf(section.heading) !== "" && textArray(section.paragraphs).length > 0
      );
    }
    case "checklist":
      return textArray(content.items).length > 0;
    case "table": {
      const headers = textArray(content.headers);
      const rows = Array.isArray(content.rows) ? content.rows.filter((row) => Array.isArray(row)) : [];
      return headers.length > 0 && rows.length > 0;
    }
    case "tree":
      return objectArray(content.nodes).length > 0;
    case "timeline":
      return objectArray(content.events).length > 0;
    case "decision":
      return objectArray(content.options).length > 0;
    case "procedure":
      return objectArray(content.steps).length > 0;
    case "schedule":
      return objectArray(content.days).length > 0;
    case "summary":
      return textArray(content.points).length > 0 || textOf(content.conclusion) !== "";
    default:
      return false;
  }
}

// Normalise les blocs reçus : types inconnus et contenus vides écartés.
export function sanitizeBlocks(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (block) =>
        block &&
        typeof block === "object" &&
        hasRenderableContent(block.type, block.content)
    )
    .slice(0, 4)
    .map((block) => ({
      type: block.type,
      title: typeof block.title === "string" ? block.title : "",
      content:
        block.content && typeof block.content === "object" && !Array.isArray(block.content)
          ? block.content
          : {},
    }));
}

export default function BlockRenderer({ block }) {
  const Component = block ? RENDERERS[block.type] : null;
  if (!Component) return null;
  return (
    <section className="block" aria-label={block.title || block.type}>
      {block.title ? <h4 className="block-title">{block.title}</h4> : null}
      <Component content={block.content || {}} />
    </section>
  );
}
