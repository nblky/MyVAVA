export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function formatFilterDateLabel(value) {
  const digits = String(value || "").replace(/\D+/g, "");
  if (digits.length === 8) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  }
  if (digits.length === 6) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}`;
  }
  return String(value || "");
}

export function safeFilePart(value) {
  return String(value || "camera")
    .trim()
    .replace(/[^a-z0-9._-]+/gi, "-")
    .replace(/^-+|-+$/g, "") || "camera";
}

export function escapeAttrSelectorValue(value) {
  const text = String(value || "");
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(text);
  }
  return text.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

export function dateOnlyValue(value) {
  const text = String(value || "").trim();
  const matched = text.match(/(\d{4}-\d{2}-\d{2})/);
  return matched ? matched[1] : "";
}

export function matchesDateRange(value, from, to) {
  const day = dateOnlyValue(value);
  if (from && (!day || day < from)) return false;
  if (to && (!day || day > to)) return false;
  return true;
}
