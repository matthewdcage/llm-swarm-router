import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const helpers = require(path.join(here, "../../static/hub-escape.js"));

test("escHtml strips tags", () => {
  const out = helpers.escHtml("<img src=x onerror=alert(1)>");
  assert.equal(out.includes("<"), false);
});

test("escAttr escapes both quotes", () => {
  const out = helpers.escAttr("a'b\"c");
  assert.equal(out.includes("'"), false);
  assert.equal(out.includes('"'), false);
});

test("safeHref rejects javascript", () => {
  assert.equal(helpers.safeHref("javascript:alert(1)"), "");
});

test("redactSecrets", () => {
  const out = helpers.redactSecrets("http://x/dl?apikey=abc123");
  assert.match(out, /apikey=\*\*\*/);
  assert.equal(out.includes("abc123"), false);
});
