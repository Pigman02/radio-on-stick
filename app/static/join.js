"use strict";

(() => {
  const form = document.getElementById("joinForm");
  const codeInput = document.getElementById("code");
  const submit = document.getElementById("submit");
  const status = document.getElementById("status");

  function normalize(value) {
    return String(value || "")
      .toUpperCase()
      .replace(/[\s_-]/g, "")
      .replace(/[IL]/g, "1")
      .replace(/O/g, "0")
      .replace(/U/g, "V");
  }

  function setStatus(text, tone = "") {
    status.textContent = text || "";
    status.dataset.tone = tone;
  }

  async function join(raw) {
    const code = normalize(raw);
    if (!/^[0-9A-HJKMNP-TV-Z]{16}$/.test(code)) {
      setStatus("请输入 16 位邀请码", "error");
      return;
    }
    submit.disabled = true;
    setStatus("正在验证…");
    try {
      const response = await fetch("/api/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
        cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setStatus(data.message || "邀请码无效", "error");
        return;
      }
      location.replace("/");
    } catch (_) {
      setStatus("网络错误，请稍后重试", "error");
    } finally {
      submit.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    join(codeInput.value);
  });

  const initial = normalize(decodeURIComponent(location.hash.slice(1)));
  if (initial) {
    codeInput.value = initial;
    join(initial);
  }
})();
