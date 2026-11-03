import { objectArray, textOf } from "./blockHelpers.js";

function TreeNode({ node, depth }) {
  const children = depth < 4 ? objectArray(node.children, 20) : [];
  return (
    <li>
      <span className="tree-label">{textOf(node.label, "Item")}</span>
      {children.length > 0 && (
        <ul>
          {children.map((child, index) => (
            <TreeNode key={index} node={child} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function TreeBlock({ content }) {
  const nodes = objectArray(content.nodes, 20);
  if (nodes.length === 0) return null;
  return (
    <ul className="tree">
      {nodes.map((node, index) => (
        <TreeNode key={index} node={node} depth={0} />
      ))}
    </ul>
  );
}
