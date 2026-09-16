// Minimal, safe markdown → HTML. Escapes all HTML first, then applies a small
// subset (headings, lists, bold/italic, code, http(s) links). Assistant chat
// output is untrusted, so escape-first is the security boundary.

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inline(s) {
  let r = s;
  r = r.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
  r = r.replace(/\*\*([^*]+)\*\*/g, (_m, c) => `<strong>${c}</strong>`);
  r = r.replace(/\*([^*]+)\*/g, (_m, c) => `<em>${c}</em>`);
  // url chars exclude quotes/space/<>) so it can't break out of the attribute
  r = r.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)"'<>]+)[^)]*\)/g,
    (_m, t, u) => `<a href="${u}" target="_blank" rel="noreferrer noopener">${t}</a>`);
  return r;
}

export function renderMarkdown(src) {
  const lines = escapeHtml(src).split("\n");
  const out = [];
  let listType = null;
  const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
  let i = 0;
  const isSpecial = (l) =>
    /^```/.test(l) || /^(#{1,3})\s+/.test(l) || /^\s*[-*]\s+/.test(l) ||
    /^\s*\d+\.\s+/.test(l) || /^\s*$/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      closeList();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre><code>${buf.join("\n")}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) { closeList(); const n = h[1].length; out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) { if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; } out.push(`<li>${inline(ul[1])}</li>`); i++; continue; }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) { if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; } out.push(`<li>${inline(ol[1])}</li>`); i++; continue; }
    if (/^\s*$/.test(line)) { closeList(); i++; continue; }
    closeList();
    const para = [line]; i++;
    while (i < lines.length && !isSpecial(lines[i])) { para.push(lines[i]); i++; }
    out.push(`<p>${para.map(inline).join("<br>")}</p>`);
  }
  closeList();
  return out.join("");
}
