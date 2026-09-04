// Column-creation helpers for the one-field quick-add flow (the ghost column).
// The inline creator asks for a LABEL only; everything else defaults — the
// field key is derived here, and the server defaults type/owner.

// Derive a stable field-key slug from a human label: lowercase, non-alphanumeric
// runs collapse to "_", leading/trailing "_" trimmed (same rule as the full
// Add-column form in ColumnConfig, kept in lockstep).
export function slugifyLabel(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

// A field key that does not collide with the sheet's existing fields: the plain
// slug when free, else slug_2, slug_3, … A label that slugs to nothing (e.g.
// all-emoji) falls back to "column".
export function uniqueField(existing: string[], label: string): string {
  const base = slugifyLabel(label) || "column";
  const taken = new Set(existing);
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) {
    const candidate = `${base}_${i}`;
    if (!taken.has(candidate)) return candidate;
  }
}
