import { useRef, useState } from "react";
import { clampBox, maskTransform } from "../lib/segments.js";

// Superpose boîtes et masques SAM sur la photo d'origine (pixels natifs).
// Tout est mémoire uniquement : l'URL blob appartient au message parent.
export default function SegmentOverlay({ previewUrl, width, height, matches, concept, truncated }) {
  const imgRef = useRef(null);
  const svgRef = useRef(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");

  const validSize = Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0;
  const list = Array.isArray(matches) ? matches : [];
  if (!previewUrl || !validSize) {
    return <p className="muted">Segmentation unavailable: preview expired.</p>;
  }

  const boxes = [];
  const paths = [];
  list.forEach((match, index) => {
    const box = clampBox(match?.box, width, height);
    if (box) boxes.push({ key: `b${index}`, box, id: match?.object_id });
    if (match?.mask) {
      const transform = maskTransform(match.mask);
      if (transform) paths.push({ key: `m${index}`, d: match.mask.svg, transform });
    }
  });

  function downloadComposite() {
    const img = imgRef.current;
    const svg = svgRef.current;
    if (!img || !svg || downloading) return;
    setDownloading(true);
    setDownloadError("");
    try {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("Canvas unavailable.");
      ctx.drawImage(img, 0, 0, width, height);
      // currentColor ne survit pas à la sérialisation : on fige la couleur calculée.
      const color = getComputedStyle(svg).color || "#4ade80";
      const svgText = new XMLSerializer().serializeToString(svg).replaceAll("currentColor", color);
      const svgUrl = URL.createObjectURL(new Blob([svgText], { type: "image/svg+xml;charset=utf-8" }));
      const overlay = new Image();
      overlay.onload = () => {
        ctx.drawImage(overlay, 0, 0, width, height);
        URL.revokeObjectURL(svgUrl);
        canvas.toBlob((blob) => {
          setDownloading(false);
          if (!blob) {
            setDownloadError("Download failed: try again.");
            return;
          }
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = "reflow-segmentation.png";
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 5000);
        }, "image/png");
      };
      overlay.onerror = () => {
        URL.revokeObjectURL(svgUrl);
        setDownloading(false);
        setDownloadError("Download failed: try again.");
      };
      overlay.src = svgUrl;
    } catch {
      setDownloading(false);
      setDownloadError("Download failed: try again.");
    }
  }

  const count = boxes.length;
  return (
    <div className="segment-block">
      <div className="segment-frame">
        <img
          ref={imgRef}
          className="segment-photo"
          src={previewUrl}
          alt={concept ? `Segmentation: ${concept}` : "Segmented image"}
          draggable={false}
        />
        {count > 0 && (
          <svg
            ref={svgRef}
            className="segment-svg"
            viewBox={`0 0 ${width} ${height}`}
            width={width}
            height={height}
            aria-hidden="true"
          >
            {paths.map((path) => (
              <path
                key={path.key}
                d={path.d}
                transform={`translate(${path.transform.x} ${path.transform.y}) scale(${path.transform.scaleX} ${path.transform.scaleY})`}
                fill="currentColor"
                fillOpacity={0.35}
                stroke="none"
              />
            ))}
            {boxes.map((entry) => (
              <rect
                key={entry.key}
                x={entry.box.left}
                y={entry.box.top}
                width={entry.box.right - entry.box.left}
                height={entry.box.bottom - entry.box.top}
                fill="none"
                stroke="currentColor"
                strokeWidth={Math.max(2, width / 400)}
              />
            ))}
          </svg>
        )}
      </div>
      <p className="muted segment-caption">
        {count === 0
          ? `No "${concept}" objects detected in this image.`
          : `${count} "${concept}" object${count > 1 ? "s" : ""} detected.`}
        {truncated ? " Partial result: too many objects." : ""}
      </p>
      {count > 0 && (
        <span className="image-actions">
          <button
            type="button"
            className="secondary small"
            onClick={downloadComposite}
            disabled={downloading}
          >
            {downloading ? "Preparing…" : "Download (PNG)"}
          </button>
        </span>
      )}
      {downloadError && (
        <p className="image-error" role="alert">
          {downloadError}
        </p>
      )}
    </div>
  );
}
