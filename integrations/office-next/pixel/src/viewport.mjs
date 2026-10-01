/** Fit the visible office, not the outer sparse edit grid. Coordinates use device pixels. */
export function fitOfficeViewport(layout, furniture, size, dpr = 1) {
  const tile = 16;
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  const include = (x, y, width, height) => {
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x + width); bottom = Math.max(bottom, y + height);
  };
  for (let row = 0; row < layout.rows; row++) {
    for (let col = 0; col < layout.cols; col++) {
      if (layout.tiles[row * layout.cols + col] !== 255) include(col * tile, row * tile, tile, tile);
    }
  }
  for (const item of furniture) {
    const height = item.sprite?.length || 0;
    const width = item.sprite?.[0]?.length || 0;
    if (width && height) include(item.x, item.y, width, height);
  }
  if (!Number.isFinite(left)) { left = 0; top = 0; right = layout.cols * tile; bottom = layout.rows * tile; }
  const width = Math.max(tile, right - left), height = Math.max(tile, bottom - top);
  // Small outer margins preserve labels while keeping the room prominent.
  const availableWidth = Math.max(tile, size.width - 32) * dpr;
  const availableHeight = Math.max(tile, size.height - 48) * dpr;
  const ideal = Math.min(availableWidth / width, availableHeight / height);
  const zoom = Math.max(1, Math.min(10, Math.floor(ideal * 4) / 4));
  return {
    zoom,
    pan: {
      x: (layout.cols * tile / 2 - (left + right) / 2) * zoom,
      y: (layout.rows * tile / 2 - (top + bottom) / 2) * zoom,
    },
    bounds: { left, top, right, bottom },
  };
}
