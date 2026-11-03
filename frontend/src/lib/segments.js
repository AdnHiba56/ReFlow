// Helpers de segmentation SAM : placement des masques SVG sur l'image source.
// Un masque vit dans son propre raster (mask.width × mask.height) ; bounds le
// positionne dans l'image source (pixels natifs). Le rendu applique :
// translation(bounds) puis mise à l'échelle (bounds / raster).
// Mémoire uniquement : aucune persistance ici.

export function maskTransform(mask) {
  const rasterW = mask?.width;
  const rasterH = mask?.height;
  const bounds = mask?.bounds;
  if (
    !Number.isFinite(rasterW) ||
    !Number.isFinite(rasterH) ||
    rasterW <= 0 ||
    rasterH <= 0 ||
    !bounds ||
    !Number.isFinite(bounds.left) ||
    !Number.isFinite(bounds.top) ||
    !Number.isFinite(bounds.right) ||
    !Number.isFinite(bounds.bottom) ||
    bounds.right <= bounds.left ||
    bounds.bottom <= bounds.top ||
    typeof mask.svg !== "string" ||
    !mask.svg
  ) {
    return null;
  }
  return {
    x: bounds.left,
    y: bounds.top,
    scaleX: (bounds.right - bounds.left) / rasterW,
    scaleY: (bounds.bottom - bounds.top) / rasterH,
  };
}

export function clampBox(box, width, height) {
  if (
    !box ||
    !Number.isFinite(box.left) ||
    !Number.isFinite(box.top) ||
    !Number.isFinite(box.right) ||
    !Number.isFinite(box.bottom)
  ) {
    return null;
  }
  const left = Math.min(Math.max(box.left, 0), width);
  const top = Math.min(Math.max(box.top, 0), height);
  const right = Math.min(Math.max(box.right, 0), width);
  const bottom = Math.min(Math.max(box.bottom, 0), height);
  if (right <= left || bottom <= top) return null;
  return { left, top, right, bottom };
}
