import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

test("pipeline logout bridge exists and clears the stored auth session", () => {
  const absolutePath = resolve(process.cwd(), "logout-bridge.html");

  assert.equal(existsSync(absolutePath), true);

  const source = readFileSync(absolutePath, "utf-8");
  assert.equal(source.includes("/auth/logout"), true);
  assert.equal(source.includes("kbf.user"), true);
  assert.equal(source.includes("postMessage"), true);
});
