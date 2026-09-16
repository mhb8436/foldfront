export function buildPopupWindowFeatures({
  screenX = 0,
  screenY = 0,
  outerWidth = 1440,
  outerHeight = 960,
  availWidth = outerWidth,
  availHeight = outerHeight,
} = {}) {
  const width = Math.max(1100, Math.min(1600, Math.round(Number(availWidth) * 0.9) || 1440));
  const height = Math.max(760, Math.min(950, Math.round(Number(availHeight) * 0.88) || 900));
  const left = Math.max(0, Math.round(Number(screenX) + Math.max(0, (Number(outerWidth) - width) / 2)));
  const top = Math.max(0, Math.round(Number(screenY) + Math.max(0, (Number(outerHeight) - height) / 2)));
  return [
    "popup=yes",
    "resizable=yes",
    "scrollbars=yes",
    "toolbar=no",
    "menubar=no",
    "location=no",
    "status=no",
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    `top=${top}`,
  ].join(",");
}

export function openPopupWindow({ open = null, url = "", name = "", features = "" } = {}) {
  const opener = typeof open === "function" ? open : null;
  if (!opener) return null;
  const popup = opener("", String(name || "").trim(), String(features || "").trim());
  if (!popup) return null;
  const targetUrl = String(url || "").trim();
  if (targetUrl) {
    try {
      if (popup.location && typeof popup.location.replace === "function") {
        popup.location.replace(targetUrl);
      } else if (popup.location) {
        popup.location.href = targetUrl;
      }
    } catch (_err) {
      // Keep the popup open even if navigation assignment is denied.
    }
  }
  try {
    if (typeof popup.focus === "function") popup.focus();
  } catch (_err) {
    // Browsers may ignore focus requests.
  }
  return popup;
}
