import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../static/login.js", import.meta.url), "utf8");

async function visitLogin({hash = "", authenticated = false, pairOk = true} = {}) {
  const elements = new Map(["login-error", "login-progress", "login-help"].map((id) => [id, {hidden: id !== "login-help", textContent: ""}]));
  const requests = [];
  let currentPath = `/login${hash}`;
  let redirect = null;
  const context = vm.createContext({
    URLSearchParams,
    document: {getElementById: (id) => elements.get(id)},
    location: {hash, search: "", replace: (url) => {redirect = url;}},
    history: {replaceState: (_state, _title, url) => {currentPath = url;}},
    fetch: async (url, options) => {
      requests.push({url, options, currentPath});
      if (url === "/api/auth") return {ok: true, json: async () => ({authenticated})};
      return {ok: pairOk, json: async () => ({authenticated: pairOk})};
    },
  });
  vm.runInContext(source, context, {filename: "login.js"});
  await new Promise(setImmediate);
  return {elements, requests, currentPath, redirect};
}

test("phone pairing removes the bearer fragment before the first request", async () => {
  const token = "A".repeat(43);
  const visit = await visitLogin({hash: `#pair=${token}`});
  assert.equal(visit.currentPath, "/login");
  assert.equal(visit.requests.length, 1);
  assert.equal(visit.requests[0].currentPath, "/login");
  assert.equal(visit.requests[0].url, "/auth/pair");
  assert.equal(visit.requests[0].options.referrerPolicy, "no-referrer");
  assert.equal(visit.requests[0].options.headers["X-DAS-Office"], "1");
  assert.equal(JSON.parse(visit.requests[0].options.body).token, token);
  assert.equal(visit.redirect, "/");
});

test("malformed pairing fragments are cleared without a network request", async () => {
  const visit = await visitLogin({hash: "#pair=short"});
  assert.equal(visit.currentPath, "/login");
  assert.equal(visit.requests.length, 0);
  assert.equal(visit.elements.get("login-error").hidden, false);
});

test("used or expired pairing codes do not open the office", async () => {
  const visit = await visitLogin({hash: `#pair=${"B".repeat(43)}`, pairOk: false});
  assert.equal(visit.redirect, null);
  assert.equal(visit.elements.get("login-error").hidden, false);
  assert.doesNotMatch(visit.elements.get("login-error").textContent, /B{43}/);
});

test("a remembered device session opens the office without another pairing", async () => {
  const visit = await visitLogin({authenticated: true});
  assert.deepEqual(visit.requests.map((request) => request.url), ["/api/auth"]);
  assert.equal(visit.redirect, "/");
});
