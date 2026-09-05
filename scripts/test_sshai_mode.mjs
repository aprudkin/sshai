#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const source = await readFile(new URL("../extensions/sshai-mode/index.ts", import.meta.url), "utf8");
const directory = await mkdtemp(join(tmpdir(), "sshai-mode-test-"));
const modulePath = join(directory, "index.ts");

// Node 24 strips the extension's type-only imports and annotations; this test mocks Pi below.
await writeFile(modulePath, source, "utf8");
const { default: register } = await import(pathToFileURL(modulePath).href);

function fixture(entries = []) {
  const handlers = new Map();
  let command;
  const appended = [];
  const statuses = [];
  const notices = [];
  const pi = {
    appendEntry(type, data) {
      appended.push({ type, data });
    },
    on(name, handler) {
      handlers.set(name, handler);
    },
    registerCommand(name, definition) {
      assert.equal(name, "sshai");
      command = definition;
    },
  };
  const ctx = {
    sessionManager: { getBranch: () => entries },
    ui: {
      theme: { bold: (value) => `bold(${value})`, fg: (color, value) => `${color}(${value})` },
      setStatus: (key, value) => statuses.push({ key, value }),
      notify: (message, level) => notices.push({ message, level }),
    },
  };
  register(pi);
  return { appended, command, ctx, handlers, notices, statuses };
}

try {
  const stateType = "sshai-execution-mode.v1";
  const empty = fixture();
  await empty.handlers.get("session_start")({}, empty.ctx);
  assert.deepEqual(empty.statuses.at(-1), { key: "sshai-mode", value: undefined });
  await empty.command.handler("status", empty.ctx);
  assert.deepEqual(empty.notices.at(-1), { message: "sshai mode: disabled", level: "info" });

  await empty.command.handler("on", empty.ctx);
  assert.deepEqual(empty.appended, [{ type: stateType, data: { enabled: true } }]);
  assert.deepEqual(empty.statuses.at(-1), { key: "sshai-mode", value: "accent(bold(sshai:on))" });
  await empty.command.handler("status", empty.ctx);
  assert.deepEqual(empty.notices.at(-1), { message: "sshai mode: enabled", level: "info" });
  await empty.command.handler("on", empty.ctx);
  assert.equal(empty.appended.length, 1, "idempotent on must not append state");
  assert.match(empty.notices.at(-1).message, /already enabled/);
  await empty.command.handler("invalid", empty.ctx);
  assert.deepEqual(empty.notices.at(-1), { message: "Usage: /sshai on | /sshai off | /sshai status", level: "warning" });

  const prompted = await empty.handlers.get("before_agent_start")({ systemPrompt: "base" }, empty.ctx);
  const promptedAgain = await empty.handlers.get("before_agent_start")({ systemPrompt: "base again" }, empty.ctx);
  assert.match(prompted.systemPrompt, /SSHAI session mode/);
  assert.equal((prompted.systemPrompt.match(/## SSHAI session mode/g) || []).length, 1);
  assert.equal((promptedAgain.systemPrompt.match(/## SSHAI session mode/g) || []).length, 1);
  await empty.command.handler("off", empty.ctx);
  assert.deepEqual(empty.appended.at(-1), { type: stateType, data: { enabled: false } });
  assert.deepEqual(empty.statuses.at(-1), { key: "sshai-mode", value: undefined });
  assert.equal(await empty.handlers.get("before_agent_start")({ systemPrompt: "base" }, empty.ctx), undefined);
  await empty.command.handler("off", empty.ctx);
  assert.equal(empty.appended.length, 2, "idempotent off must not append state");

  const restored = fixture([
    { type: "custom", customType: stateType, data: { enabled: false } },
    { type: "custom", customType: "other", data: { enabled: false } },
    { type: "custom", customType: stateType, data: { enabled: true } },
  ]);
  await restored.handlers.get("session_start")({}, restored.ctx);
  assert.match(restored.statuses.at(-1).value, /sshai:on/, "latest durable custom state restores");
  restored.ctx.sessionManager.getBranch = () => [
    { type: "custom", customType: stateType, data: { enabled: true } },
    { type: "custom", customType: stateType, data: { enabled: false } },
  ];
  await restored.handlers.get("session_tree")({}, restored.ctx);
  assert.deepEqual(restored.statuses.at(-1), { key: "sshai-mode", value: undefined });
  await restored.handlers.get("session_shutdown")({}, restored.ctx);
  assert.deepEqual(restored.statuses.at(-1), { key: "sshai-mode", value: undefined });

  const completions = empty.command.getArgumentCompletions("O");
  assert.deepEqual(completions.map((item) => item.value), ["on", "off"]);
  assert.equal(empty.command.getArgumentCompletions("nope"), null);
  console.log("sshai mode behavior passed");
} finally {
  await rm(directory, { recursive: true, force: true });
}
