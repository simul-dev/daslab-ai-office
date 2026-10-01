"use strict";

(() => {
  const fragment = location.hash;
  const failed = new URLSearchParams(location.search).has("failed");
  // The one-time bearer token stays in a URL fragment, which is not sent to the
  // server, and is removed before this page makes any network request.
  history.replaceState(null, "", "/login");
  const match = /^#pair=([A-Za-z0-9_-]{43})$/.exec(fragment);
  const error = document.getElementById("login-error");
  const progress = document.getElementById("login-progress");
  const help = document.getElementById("login-help");

  function showError(message) {
    progress.hidden = true;
    help.hidden = false;
    error.textContent = message;
    error.hidden = false;
  }

  if (fragment && !match) {
    showError("연결 주소를 읽지 못했습니다. PC에서 새 QR 코드를 만든 뒤 다시 스캔해 주세요.");
    return;
  }

  if (match) {
    help.hidden = true;
    progress.hidden = false;
    fetch("/auth/pair", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-DAS-Office": "1"},
      credentials: "same-origin",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      body: JSON.stringify({token: match[1]}),
    }).then(async (response) => {
      if (!response.ok) throw new Error("pairing failed");
      const result = await response.json();
      if (result.authenticated !== true) throw new Error("pairing incomplete");
      location.replace("/");
    }).catch(() => showError("연결 코드가 만료됐거나 이미 사용됐습니다. PC에서 새 QR 코드를 만든 뒤 다시 스캔해 주세요."));
    return;
  }

  if (failed) showError("연결하지 못했습니다. PC에서 새 QR 코드를 만든 뒤 다시 스캔해 주세요.");
  fetch("/api/auth", {credentials: "same-origin", cache: "no-store", referrerPolicy: "no-referrer"})
    .then((response) => response.ok ? response.json() : null)
    .then((status) => { if (status?.authenticated) location.replace("/"); })
    .catch(() => {});
})();
