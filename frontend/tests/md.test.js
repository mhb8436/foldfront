import test from "node:test";
import assert from "node:assert/strict";
const { renderMarkdown } = await import("../lib/md.js");

test("bold, italic, inline code", () => {
  assert.match(renderMarkdown("**b** *i* `c`"), /<strong>b<\/strong>/);
  assert.match(renderMarkdown("*i*"), /<em>i<\/em>/);
  assert.match(renderMarkdown("`c`"), /<code>c<\/code>/);
});
test("headings and lists", () => {
  assert.match(renderMarkdown("# H"), /<h1>H<\/h1>/);
  assert.match(renderMarkdown("- a\n- b"), /<ul><li>a<\/li><li>b<\/li><\/ul>/);
  assert.match(renderMarkdown("1. a\n2. b"), /<ol><li>a<\/li><li>b<\/li><\/ol>/);
});
test("fenced code block", () => {
  const h = renderMarkdown("```\nx=1\n```");
  assert.match(h, /<pre><code>x=1<\/code><\/pre>/);
});
test("http links only, target/rel set", () => {
  assert.match(renderMarkdown("[t](https://a.com)"), /<a href="https:\/\/a\.com" target="_blank" rel="noreferrer noopener">t<\/a>/);
  // non-http link rendered as plain text (no anchor)
  assert.doesNotMatch(renderMarkdown("[t](javascript:alert(1))"), /<a /);
});
test("XSS is escaped, not executed", () => {
  const h = renderMarkdown('<img src=x onerror=alert(1)> <script>bad()</script>');
  assert.doesNotMatch(h, /<img/);
  assert.doesNotMatch(h, /<script>/);
  assert.match(h, /&lt;img/);
});
test("quote in link url cannot break the attribute", () => {
  const h = renderMarkdown('[t](https://a.com" onmouseover=x)');
  assert.doesNotMatch(h, /onmouseover=/);
});
test("plain text becomes a paragraph", () => {
  assert.match(renderMarkdown("hello world"), /<p>hello world<\/p>/);
});
