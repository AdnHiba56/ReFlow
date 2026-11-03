import { objectArray, textArray, textOf } from "./blockHelpers.js";

export default function ArticleBlock({ content }) {
  const introduction = textOf(content.introduction, "");
  const sections = objectArray(content.sections, 20)
    .map((section) => ({
      heading: textOf(section.heading, ""),
      paragraphs: textArray(section.paragraphs, 30),
    }))
    .filter((section) => section.heading && section.paragraphs.length > 0);
  const conclusion = textOf(content.conclusion, "");
  if (!introduction || sections.length === 0 || !conclusion) return null;

  return (
    <article className="article">
      <p className="article-intro">{introduction}</p>
      {sections.map((section, index) => (
        <section key={index} className="article-section">
          <h5>{section.heading}</h5>
          {section.paragraphs.map((paragraph, i) => (
            <p key={i}>{paragraph}</p>
          ))}
        </section>
      ))}
      <p className="article-conclusion">{conclusion}</p>
    </article>
  );
}
