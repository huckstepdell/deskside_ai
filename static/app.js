/* Dell AI demo dashboard — frontend logic */
"use strict";

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const COLORS = {
  gpu: css("--c-gpu"), mem: css("--c-mem"), pwr: css("--c-pwr"),
  tmp: css("--c-tmp"), cpu: css("--c-cpu"),
};

// Deployment config (role models, remote hosts) — fetched at boot; fallbacks
// keep the UI usable if the fetch loses the race with first user action.
let CFG = { roles: {}, remote_hosts: [] };
const roleModel = (r, fb) => CFG.roles[r] || fb || "";

/* ============================================================
   JSON fetch with retry — Firefox reuses just-closed keep-alive
   sockets and server restarts drop connections; both surface as
   "NetworkError when attempting to fetch resource". Retry those;
   real HTTP errors (4xx/5xx) surface immediately.
   ============================================================ */
async function apiJSON(url, body) {
  for (const delay of [0, 400, 1500, 3000]) {
    if (delay) await new Promise((r) => setTimeout(r, delay));
    let resp;
    try {
      resp = await fetch(url, body === undefined ? {} : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (err) {
      if (delay === 3000) throw new Error("Can't reach the demo server — is the dashboard container running?");
      continue;
    }
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).detail; } catch {}
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }
}

/* ============================================================
   SSE helper: POST-based server-sent-event streams via fetch
   ============================================================ */
async function streamSSE(url, body, handlers, signal) {
  const doFetch = () => fetch(url, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  // Retry with backoff: covers Firefox reusing a just-closed keep-alive
  // socket AND brief server restarts.
  let resp;
  for (const delay of [0, 400, 1500, 3000]) {
    if (delay) await new Promise((r) => setTimeout(r, delay));
    try {
      resp = await doFetch();
      break;
    } catch (err) {
      if (err.name === "AbortError") throw err; // user cancel — never retry
      if (delay === 3000) throw new Error("Can't reach the demo server — is the dashboard container running?");
    }
  }
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data && handlers[event]) handlers[event](JSON.parse(data));
    }
  }
}

/* ============================================================
   Streaming answer target: handles token + thinking streams.
   Reasoning models (qwen3.6 …) emit "thinking" before answering —
   show it live and dim, collapse it once the real answer flows.
   ============================================================ */
/* Model answers arrive as markdown — render it (headings, bullets, bold)
   instead of showing raw ### and ** to the audience. Raw HTML is escaped
   before parsing so model output can't inject markup. */
function mdRender(text) {
  const safe = text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  try { return marked.parse(safe); } catch { return safe; }
}

function streamTarget(container) {
  container.textContent = "";
  const thinkEl = document.createElement("div");
  thinkEl.className = "think";
  thinkEl.hidden = true;
  const answerEl = document.createElement("div");
  answerEl.className = "md-body";
  answerEl.textContent = "…";
  container.append(thinkEl, answerEl);
  let think = "", answer = "";
  return {
    get answer() { return answer; },
    onData(d) {
      if (d.status && !think && !answer) {
        answerEl.textContent = d.status;
      }
      if (d.think) {
        think += d.think;
        thinkEl.hidden = false;
        thinkEl.textContent = `Thinking… ${think}`;
        thinkEl.scrollTop = thinkEl.scrollHeight; // keep the newest reasoning visible
        if (!answer) answerEl.textContent = "…";
      }
      if (d.token) {
        answer += d.token;
        answerEl.innerHTML = mdRender(answer);
      }
    },
    finish(stats, label) {
      if (think) {
        const det = document.createElement("details");
        det.className = "think";
        const sum = document.createElement("summary");
        sum.textContent = "Show reasoning";
        det.append(sum, document.createTextNode(think));
        thinkEl.replaceWith(det);
      } else {
        thinkEl.remove();
      }
      if (stats) {
        const s = document.createElement("span");
        s.className = "stats";
        s.innerHTML = `<b>${stats.tok_per_sec ?? "?"}</b> tokens/sec · ${label}`;
        container.appendChild(s);
      }
    },
    setError(msg) { answerEl.textContent = msg; },
  };
}

/* ============================================================
   Router
   ============================================================ */
const PAGES = ["overview", "chat", "compare", "bench", "buildoff", "vision", "imagegen",
  "rag", "runbooks", "batch", "agentworks", "agent", "casework", "studio", "docout"];
// scenario pages register themselves here after /api/scenarios loads
function route() {
  const page = location.hash.replace("#", "") || "overview";
  const target = PAGES.includes(page) ? page : "overview";
  for (const p of PAGES) {
    const el = $(`page-${p}`);
    if (el) el.hidden = p !== target;
  }
  document.querySelectorAll(".nav-link").forEach((a) =>
    a.classList.toggle("active", a.dataset.page === target));
}
window.addEventListener("hashchange", route);

/* ============================================================
   Telemetry: one EventSource feeds the strip + overview charts
   ============================================================ */
const HISTORY_POINTS = 240; // 120s at 2Hz

function makeSpark(canvasId, color) {
  return new Chart($(canvasId), {
    type: "line",
    data: { labels: [], datasets: [{ data: [], borderColor: color, borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, min: 0 } },
      events: [],
    },
  });
}

function makeLineChart(canvasId, color, { max = undefined, unit = "" } = {}) {
  return new Chart($(canvasId), {
    type: "line",
    data: { labels: [], datasets: [{ data: [], borderColor: color, borderWidth: 2, pointRadius: 0, fill: { target: "origin", above: color + "22" }, tension: 0.3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          callbacks: { label: (ctx) => `${ctx.parsed.y}${unit}`, title: (items) => items[0]?.label ?? "" },
        },
      },
      scales: {
        x: { ticks: { display: false }, grid: { display: false }, border: { color: css("--grid") } },
        y: {
          min: 0, max,
          ticks: { color: css("--ink-3"), font: { size: 11 }, maxTicksLimit: 5 },
          grid: { color: css("--grid") },
          border: { display: false },
        },
      },
    },
  });
}

const sparks = {
  gpu: makeSpark("s-gpu", COLORS.gpu),
  mem: makeSpark("s-mem", COLORS.mem),
  pwr: makeSpark("s-pwr", COLORS.pwr),
  tmp: makeSpark("s-tmp", COLORS.tmp),
  cpu: makeSpark("s-cpu", COLORS.cpu),
};
const charts = {
  gpu: makeLineChart("chart-gpu", COLORS.gpu, { max: 100, unit: "%" }),
  mem: makeLineChart("chart-mem", COLORS.mem, { unit: " GB" }),
  pwr: makeLineChart("chart-pwr", COLORS.pwr, { unit: " W" }),
};
const coresChart = new Chart($("chart-cores"), {
  type: "bar",
  data: { labels: [], datasets: [{ data: [], backgroundColor: COLORS.cpu, borderRadius: 3, barPercentage: 0.8 }] },
  options: {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: {
      legend: { display: false },
      tooltip: { displayColors: false, callbacks: { label: (ctx) => `${ctx.parsed.y}%` } },
    },
    scales: {
      x: { ticks: { color: css("--ink-3"), font: { size: 10 } }, grid: { display: false } },
      y: { min: 0, max: 100, ticks: { color: css("--ink-3"), font: { size: 11 }, maxTicksLimit: 5 }, grid: { color: css("--grid") }, border: { display: false } },
    },
  },
});

function pushPoint(chart, label, value, cap = HISTORY_POINTS) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > cap) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
}

let strideCounter = 0;
function applySample(s, redraw = true) {
  const label = new Date(s.t * 1000).toLocaleTimeString();
  pushPoint(sparks.gpu, label, s.gpu_util);
  pushPoint(sparks.mem, label, s.mem_used_gb);
  pushPoint(sparks.pwr, label, s.gpu_power);
  pushPoint(sparks.tmp, label, s.gpu_temp);
  pushPoint(sparks.cpu, label, s.cpu_util);
  pushPoint(charts.gpu, label, s.gpu_util);
  pushPoint(charts.mem, label, s.mem_used_gb);
  pushPoint(charts.pwr, label, s.gpu_power);

  if (redraw) {
    $("v-gpu").textContent = Math.round(s.gpu_util);
    $("v-mem").textContent = s.mem_used_gb.toFixed(1);
    $("v-memtotal").textContent = Math.round(s.mem_total_gb);
    $("v-pwr").textContent = Math.round(s.gpu_power);
    $("v-tmp").textContent = Math.round(s.gpu_temp);
    $("v-cpu").textContent = Math.round(s.cpu_util);

    coresChart.data.labels = s.cpu_per_core.map((_, i) => i + 1);
    coresChart.data.datasets[0].data = s.cpu_per_core;

    // redraw sparks every sample; big charts every 2nd (still 1Hz)
    Object.values(sparks).forEach((c) => c.update("none"));
    if (strideCounter++ % 2 === 0) {
      Object.values(charts).forEach((c) => c.update("none"));
      coresChart.update("none");
    }
  }
}

function connectTelemetry() {
  const es = new EventSource("/api/telemetry/stream");
  es.addEventListener("history", (e) => {
    const samples = JSON.parse(e.data);
    samples.slice(0, -1).forEach((s) => applySample(s, false));
    if (samples.length) applySample(samples[samples.length - 1]);
  });
  es.onmessage = (e) => applySample(JSON.parse(e.data));
  es.onerror = () => {
    es.close();
    setTimeout(connectTelemetry, 3000); // server restarted? reconnect
  };
}

/* ============================================================
   Memory view: which models occupy the unified pool right now
   ============================================================ */
// color follows the entity: a model keeps its color for the whole session
const MEM_SLOT_COLORS = [COLORS.gpu, COLORS.cpu, COLORS.pwr, COLORS.tmp, COLORS.mem];
const memColorByModel = new Map();
function memColor(name) {
  if (!memColorByModel.has(name)) {
    memColorByModel.set(name, MEM_SLOT_COLORS[memColorByModel.size % MEM_SLOT_COLORS.length]);
  }
  return memColorByModel.get(name);
}

function expiryText(expiresAt) {
  if (!expiresAt) return "";
  const mins = Math.round((new Date(expiresAt) - Date.now()) / 60000);
  if (mins <= 0 || mins > 24 * 60) return "";
  return `frees in ~${mins} min`;
}

async function refreshMemoryView() {
  let data;
  try {
    data = await apiJSON("/api/memory");
  } catch {
    return;
  }
  $("memTotalLabel").textContent = Math.round(data.total_gb);
  window.__memTotal = data.total_gb; // arena uses this for pairing fit checks
  const modelsGb = data.models.reduce((sum, m) => sum + m.size_gb, 0);
  const otherGb = Math.max(0, data.used_gb - modelsGb);
  const pct = (gb) => `${(100 * gb) / data.total_gb}%`;

  const segs = data.models.map((m) =>
    `<div class="mem-seg" style="width:${pct(m.size_gb)};background:${memColor(m.name)}" title="${m.label} — ${m.size_gb} GB"></div>`);
  segs.push(`<div class="mem-seg other" style="width:${pct(otherGb)}" title="System & apps — ${otherGb.toFixed(1)} GB"></div>`);
  $("membar").innerHTML = segs.join("");

  const chips = data.models.map((m) => {
    const exp = expiryText(m.expires_at);
    return `<span class="chip"><span class="dot" style="background:${memColor(m.name)}"></span>
      <b>${m.label}</b> ${m.size_gb} GB · ${m.source}${exp ? ` <span class="expiry">· ${exp}</span>` : ""}</span>`;
  });
  chips.push(`<span class="chip"><span class="dot" style="background:rgba(255,255,255,0.16)"></span>System &amp; apps ${otherGb.toFixed(1)} GB</span>`);
  chips.push(`<span class="chip"><span class="dot" style="background:rgba(255,255,255,0.04)"></span>Free ${(data.total_gb - data.used_gb).toFixed(1)} GB</span>`);
  $("memLegend").innerHTML = chips.join("") + (data.models.length ? "" :
    `<span class="mem-empty">No AI models loaded yet — run a Chat or Image demo and watch them appear here.</span>`);
}
setInterval(refreshMemoryView, 3000);

/* ============================================================
   Model dock: manual load / unload from the Overview page
   ============================================================ */
let dockItems = [];
// clicked-but-not-yet-confirmed actions; the server's "busy" field takes
// over from the first status poll after the POST
const dockPending = new Map(); // name → "loading" | "unloading"

function renderDock() {
  $("modelDock").innerHTML = dockItems.map((m) => {
    const busy = m.busy || dockPending.get(m.name);
    const exp = expiryText(m.expires_at);
    const status = busy
      ? `<span class="dock-status busy">⟳ ${busy === "loading" ? "loading into memory…" : "unloading…"}</span>`
      : m.loaded
        ? `<span class="dock-status on">● in memory${exp ? ` · ${exp.replace("frees in ", "")}` : ""}</span>`
        : `<span class="dock-status off">○ on disk</span>`;
    const canLoad = m.name !== "sdxl" || m.cached;
    const btn = busy ? ""
      : m.loaded
        ? `<button class="btn dock-btn" data-dock="unload" data-name="${m.name}">Unload</button>`
        : canLoad
          ? `<button class="btn dock-btn" data-dock="load" data-name="${m.name}">Load</button>`
          : `<span class="dock-status off">not downloaded</span>`;
    return `<div class="dock-row">
      <div class="dock-info"><b>${m.label}</b><span>${m.size_gb} GB</span></div>
      ${status}${btn}</div>`;
  }).join("");
  const errs = dockItems.filter((m) => m.error);
  $("dockMsg").textContent = errs.length ? `⚠ ${errs[0].label}: ${errs[0].error}` : "";
  // one click clears the pool — the loadout demos leave big copies resident
  const resident = dockItems.filter((m) => m.loaded && !m.busy && !dockPending.has(m.name));
  $("dockUnloadAll").hidden = resident.length < 2;
}

async function refreshModelDock() {
  try {
    dockItems = await apiJSON("/api/models/status");
  } catch {
    return;
  }
  // server state now reflects (or has finished) anything we kicked off
  for (const m of dockItems) if (!m.busy) dockPending.delete(m.name);
  const busyNow = dockItems.some((m) => m.busy) || dockPending.size > 0;
  renderDock();
  if (refreshModelDock.wasBusy && !busyNow) refreshPreflight(); // an action just finished
  refreshModelDock.wasBusy = busyNow;
}
setInterval(refreshModelDock, 3000);

$("dockUnloadAll").addEventListener("click", async () => {
  const resident = dockItems.filter((m) => m.loaded && !m.busy && !dockPending.has(m.name));
  for (const m of resident) dockPending.set(m.name, "unloading");
  renderDock();
  for (const m of resident) {
    try {
      await apiJSON("/api/models/unload", { name: m.name });
    } catch (err) {
      dockPending.delete(m.name);
      $("dockMsg").textContent = `⚠ ${err.message}`;
    }
  }
  renderDock();
});

$("modelDock").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-dock]");
  if (!btn) return;
  const { dock: action, name } = btn.dataset;
  dockPending.set(name, action === "load" ? "loading" : "unloading");
  renderDock();
  try {
    await apiJSON(`/api/models/${action}`, { name });
  } catch (err) {
    dockPending.delete(name);
    $("dockMsg").textContent = `⚠ ${err.message}`;
    renderDock();
  }
});

/* ============================================================
   Cloud savings: cumulative local tokens vs. metered API rates
   ============================================================ */
function fmtCount(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return String(n);
}

async function refreshSavings() {
  let s;
  try {
    s = await apiJSON("/api/tokens");
  } catch {
    return;
  }
  $("v-saved").textContent =
    s.saved_usd >= 100 ? `$${Math.round(s.saved_usd)}`
    : s.saved_usd >= 1 ? `$${s.saved_usd.toFixed(2)}`
    : `$${s.saved_usd.toFixed(4)}`; // fine-grained early on so it visibly climbs
  const parts = [`${fmtCount(s.total_tokens)} tokens`];
  if (s.images) parts.push(`${s.images} image${s.images === 1 ? "" : "s"}`);
  $("v-savedsub").textContent = `${parts.join(" · ")} — all generated locally`;
}
setInterval(refreshSavings, 1000);

/* ============================================================
   Overview: device info + preflight
   ============================================================ */
async function loadOverview() {
  const dev = await apiJSON("/api/device");
  const specs = [
    ["Superchip", dev.superchip],
    ["GPU", dev.gpu],
    ["CPU", dev.cpu],
    ["Memory", dev.memory],
    ["CUDA", dev.cuda ? `${dev.cuda} · driver ${dev.driver}` : "—"],
  ];
  $("specGrid").innerHTML = specs
    .map(([k, v]) => `<div class="spec"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join("");
  refreshPreflight();
}

async function refreshPreflight() {
  const pf = await apiJSON("/api/preflight");
  const item = (ok, label, sub = "") =>
    `<li><span class="pf-light ${ok ? "pf-ok" : "pf-bad"}"></span>${label}${sub ? `<span class="sub">${sub}</span>` : ""}</li>`;
  $("preflight").innerHTML = [
    item(pf.ollama, "AI engine (Ollama)", pf.ollama ? "running" : "not running"),
    item(pf.chat_models.length > 0, "Chat models", `${pf.chat_models.length} ready`),
    item(pf.vision_model, "Vision model", pf.vision_model ? "ready" : "missing"),
    item(pf.embed_model, "Docs Q&A index model", pf.embed_model ? "ready" : "missing"),
    item(pf.sdxl_cached, "Image generation (SDXL)", pf.sdxl_loaded ? "warmed up" : pf.sdxl_cached ? "cached — warm up before call" : "not downloaded"),
    item(pf.runbooks > 0, "Runbooks", `${pf.runbooks} available offline`),
  ].join("");
}

/* ============================================================
   Chat
   ============================================================ */
let chatModel = null;
let chatHistory = [];
let chatBusy = false;

async function loadModels() {
  const models = await apiJSON("/api/models");
  $("modelRow").innerHTML = models
    .map((m) => `
      <button class="model-card" data-model="${m.name}">
        <div class="m-label">${m.label}</div>
        <div class="m-blurb">${m.blurb}</div>
        <div class="m-size">${m.size_gb} GB on disk</div>
      </button>`)
    .join("");
  const cards = document.querySelectorAll(".model-card");
  cards.forEach((card) =>
    card.addEventListener("click", () => {
      cards.forEach((c) => c.classList.remove("selected"));
      card.classList.add("selected");
      chatModel = card.dataset.model;
    }));
  // sensible default: first (27B all-rounder)
  if (cards.length) cards[0].click();
}

function addMsg(role, text = "") {
  $("chatEmpty")?.remove();
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  $("chatThread").appendChild(div);
  $("chatThread").scrollTop = $("chatThread").scrollHeight;
  return div;
}

async function sendChat(text) {
  if (chatBusy || !text.trim() || !chatModel) return;
  chatBusy = true;
  $("chatSend").disabled = true;
  addMsg("user", text);
  chatHistory.push({ role: "user", content: text });
  const bubble = addMsg("assistant");
  const target = streamTarget(bubble);
  try {
    await streamSSE("/api/chat", { model: chatModel, messages: chatHistory }, {
      message: (d) => {
        target.onData(d);
        $("chatThread").scrollTop = $("chatThread").scrollHeight;
      },
      done: (stats) => target.finish(stats, `${stats.tokens} tokens · generated on this device`),
      error: (d) => target.setError(`⚠ ${d.error}`),
    });
    if (target.answer) chatHistory.push({ role: "assistant", content: target.answer });
  } catch (err) {
    target.setError(`⚠ ${err.message}`);
  } finally {
    chatBusy = false;
    $("chatSend").disabled = false;
  }
}

$("chatForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("chatInput").value;
  $("chatInput").value = "";
  sendChat(text);
});
document.querySelectorAll(".suggestion").forEach((b) =>
  b.addEventListener("click", () => { $("chatInput").value = b.dataset.fill; $("chatInput").focus(); }));

/* Scenario pages: jump to a demo page with a prompt pre-filled, or open a
   playbook. Delegated so dynamically rendered scenario pages work too. */
document.addEventListener("click", (e) => {
  const rb = e.target.closest("[data-runbook]");
  if (rb) {
    location.hash = "runbooks";
    openRunbook(rb.dataset.runbook, `https://build.nvidia.com/spark/${rb.dataset.runbook}`);
    return;
  }
  const b = e.target.closest("[data-goto]");
  if (!b) return;
  location.hash = b.dataset.goto;
  if (b.dataset.ragdoc) {  // pre-select the sample document
    const opt = [...$("ragDocSelect").options].find((o) => o.value.includes(b.dataset.ragdoc));
    if (opt) $("ragDocSelect").value = opt.value;
  }
  const input = b.dataset.fillId && $(b.dataset.fillId);
  if (input) {
    input.value = b.dataset.fill || "";
    input.focus();
  }
  if (b.dataset.goto === "docout" && b.dataset.fill) docoutRun(); // deliverable demos auto-start
});

/* ============================================================
   Vision
   ============================================================ */
let visionB64 = null;

function setVisionImage(file) {
  if (!file || !file.type.startsWith("image/")) return;
  // The vision model only decodes JPEG/PNG, but browsers happily preview
  // WebP/AVIF/etc — so re-encode anything displayable to a bounded JPEG.
  // Also shrinks giant phone photos, which speeds up inference.
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    URL.revokeObjectURL(url);
    const MAX = 1600;
    const scale = Math.min(1, MAX / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; // transparent PNGs get a white backdrop, not black
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
    visionB64 = dataUrl.split(",")[1];
    $("visionPreview").src = dataUrl;
    $("visionPreview").hidden = false;
    $("dzInner").hidden = true;
    $("visionAsk").disabled = false;
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    const card = $("visionAnswer");
    card.hidden = false;
    card.textContent = `⚠ Couldn't read "${file.name}" — this browser can't decode that image format. Try a JPEG or PNG.`;
  };
  img.src = url;
}

const dz = $("dropzone");
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dz.classList.remove("dragover");
  setVisionImage(e.dataTransfer.files[0]);
});
dz.addEventListener("click", () => $("visionFile").click());
$("dzBrowse").addEventListener("click", (e) => { e.stopPropagation(); $("visionFile").click(); });
$("visionFile").addEventListener("change", (e) => setVisionImage(e.target.files[0]));

$("visionForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!visionB64) return;
  const q = $("visionQuestion").value.trim() ||
    "Describe this image in detail for a general audience.";
  const card = $("visionAnswer");
  card.hidden = false;
  $("visionAsk").disabled = true;
  const target = streamTarget(card);
  target.setError("Looking at the image…");
  try {
    await streamSSE("/api/chat", {
      model: roleModel("vision", "gemma4:26b-a4b-it-qat"),
      messages: [{ role: "user", content: q }],
      images: [visionB64],
    }, {
      message: (d) => target.onData(d),
      done: (stats) => target.finish(stats, "vision model ran locally"),
      error: (d) => target.setError(`⚠ ${d.error}`),
    });
  } catch (err) {
    target.setError(`⚠ ${err.message}`);
  } finally {
    $("visionAsk").disabled = false;
  }
});

/* ============================================================
   Image Studio
   ============================================================ */
$("igSteps").addEventListener("input", () => { $("igStepsVal").textContent = $("igSteps").value; });

$("igWarmup").addEventListener("click", async () => {
  $("igWarmup").disabled = true;
  $("igStatus").textContent = "Loading SDXL onto the GPU…";
  try {
    // background load (same path as the model dock) + poll until resident
    await apiJSON("/api/models/load", { name: "sdxl" });
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const sdxl = (await apiJSON("/api/models/status")).find((m) => m.name === "sdxl");
      if (sdxl?.error) throw new Error(sdxl.error);
      if (sdxl?.loaded) {
        $("igStatus").textContent = "Model warmed up — generations will start instantly.";
        refreshPreflight();
        return;
      }
      $("igStatus").textContent = `Loading SDXL onto the GPU… ${(i + 1) * 2}s`;
    }
    throw new Error("Warm-up is taking unusually long — check the server log.");
  } catch (err) {
    $("igStatus").textContent = `⚠ ${err.message}`;
  } finally {
    $("igWarmup").disabled = false;
  }
});

$("igGenerate").addEventListener("click", async () => {
  const prompt = $("igPrompt").value.trim();
  if (!prompt) return;
  $("igGenerate").disabled = true;
  $("igProgress").hidden = false;
  $("igProgressFill").style.width = "0%";
  $("igStatus").textContent = "Starting…";
  try {
    await streamSSE("/api/imagegen", {
      prompt,
      negative_prompt: $("igNegative").value.trim(),
      steps: parseInt($("igSteps").value, 10),
    }, {
      message: (d) => {
        if (d.type === "status") $("igStatus").textContent = d.status;
        else if (d.type === "progress") {
          $("igStatus").textContent = `Denoising — step ${d.step} of ${d.total} (GPU at full tilt ↑)`;
          $("igProgressFill").style.width = `${(100 * d.step) / d.total}%`;
        } else if (d.type === "image") {
          $("igImage").src = `data:image/png;base64,${d.png_b64}`;
          $("igImage").hidden = false;
          $("igPlaceholder").hidden = true;
          $("igStatus").textContent = "Done — generated entirely on this device.";
        } else if (d.type === "error") {
          $("igStatus").textContent = `⚠ ${d.error}`;
        }
      },
    });
  } catch (err) {
    $("igStatus").textContent = `⚠ ${err.message}`;
  } finally {
    $("igGenerate").disabled = false;
    $("igProgress").hidden = true;
  }
});

/* ============================================================
   Docs Q&A (RAG)
   ============================================================ */
async function loadRagDocs(selectName) {
  const docs = await apiJSON("/api/rag/docs");
  const sel = $("ragDocSelect");
  sel.innerHTML = docs.length
    ? docs.map((d) => `<option value="${d.doc_id}">${d.name} (${d.pages} pages)</option>`).join("")
    : `<option value="">No documents yet — upload a PDF</option>`;
  if (selectName) sel.value = selectName;
}

$("ragFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("ragStatus").textContent = `Reading and indexing ${file.name} on-device…`;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/rag/upload", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const doc = await r.json();
    $("ragStatus").textContent = `Indexed: ${doc.chunks} passages from ${doc.pages} pages.`;
    await loadRagDocs(doc.doc_id);
  } catch (err) {
    $("ragStatus").textContent = `⚠ ${err.message}`;
  }
  e.target.value = "";
});

$("ragForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const doc_id = $("ragDocSelect").value;
  const question = $("ragQuestion").value.trim();
  if (!doc_id || !question) return;
  const card = $("ragAnswer");
  card.hidden = false;
  $("ragAsk").disabled = true;
  $("ragPassages").hidden = true;
  const target = streamTarget(card);
  target.setError("Searching the document…");
  try {
    await streamSSE("/api/rag/ask", { doc_id, question }, {
      passages: (ps) => {
        $("ragPassages").hidden = false;
        $("ragPassageList").innerHTML = ps
          .map((p) => `<div class="passage"><span class="score">relevance ${(p.score * 100).toFixed(0)}%</span><br>${p.chunk.slice(0, 400)}…</div>`)
          .join("");
      },
      message: (d) => target.onData(d),
      done: (stats) => target.finish(stats, "document never left this device"),
      error: (d) => target.setError(`⚠ ${d.error}`),
    });
  } catch (err) {
    target.setError(`⚠ ${err.message}`);
  } finally {
    $("ragAsk").disabled = false;
  }
});

/* ============================================================
   Runbooks
   ============================================================ */
async function loadRunbooks() {
  const items = await apiJSON("/api/runbooks");
  const card = (r) => `
      <button class="rb-card ${r.featured ? "" : "rb-compact"}" data-slug="${r.slug}" data-url="${r.url}" ${r.available ? "" : "disabled"}>
        ${r.tag ? `<span class="rb-tag">${r.tag}</span>` : ""}
        <span class="rb-title">${r.title}</span>
        <span class="rb-blurb">${r.blurb}</span>
        ${r.status ? `<span class="rb-status">● ${r.status}</span>` : ""}
      </button>`;
  const featured = items.filter((r) => r.featured);
  const rest = items.filter((r) => !r.featured);
  $("rbGrid").innerHTML = featured.map(card).join("") +
    (rest.length ? `<div class="rb-divider">Everything else in the local playbook library — ${rest.length} more, all offline</div>` : "") +
    rest.map(card).join("");
  document.querySelectorAll(".rb-card").forEach((c) =>
    c.addEventListener("click", () => openRunbook(c.dataset.slug, c.dataset.url)));
}

async function openRunbook(slug, url) {
  const r = await fetch(`/api/runbooks/${slug}`);
  if (!r.ok) return;
  const { markdown } = await r.json();
  $("rbArticle").innerHTML = marked.parse(markdown);
  $("rbExternal").href = url;
  $("rbGrid").hidden = true;
  $("rbReader").hidden = false;
  window.scrollTo({ top: 0 });
}
$("rbBack").addEventListener("click", () => {
  $("rbReader").hidden = true;
  $("rbGrid").hidden = false;
});

/* ============================================================
   Model Comparison — one prompt, two models, studied in detail
   ============================================================ */
// The cross-machine preset binds to whichever remote host the deployment
// has (if any) — resolved in loadArena, not hardcoded to one box. Presets
// with a `role` bind both sides to that role's model the same way. Each
// side may carry a loadout: ctx ("auto" | number | "max"), think ("auto" | bool),
// temp ("auto" | number).
const AR_PRESETS = [
  { label: "Two machines, one model", crossMachine: true,
    a: { host: "gb10", model: "" }, b: { host: "", model: "" },
    prompt: "In about 150 words, explain why running AI models on local hardware changes the economics for a mid-size business." },
  { label: "Same model, two loadouts", role: "workhorse",
    a: { host: "gb10", model: "", ctx: 4096 }, b: { host: "gb10", model: "", ctx: "max" },
    prompt: "In 100 words, what should a small business know before adopting AI?" },
  { label: "Watch it think", roles: ["reasoner", "fast"],
    a: { host: "gb10", model: "" }, b: { host: "gb10", model: "" },
    prompt: "Alice has 3 brothers and 2 sisters. How many sisters does Alice's brother have? Explain your answer." },
  { label: "Thinking on vs off", role: "reasoner",
    a: { host: "gb10", model: "", think: true }, b: { host: "gb10", model: "", think: false },
    prompt: "Alice has 3 brothers and 2 sisters. How many sisters does Alice's brother have? Explain your answer." },
  { label: "Precise vs creative", role: "workhorse",
    a: { host: "gb10", model: "", temp: 0 }, b: { host: "gb10", model: "", temp: 1.2 },
    prompt: "Write a two-sentence product description for a stainless steel water bottle." },
  { label: "The speed paradox", roles: ["fast", "workhorse"],
    a: { host: "gb10", model: "" }, b: { host: "gb10", model: "" },
    prompt: "List 10 practical uses for local AI in a small business — one line each, no preamble." },
  { label: "David vs Goliath", roles: ["fast", "reasoner"],
    a: { host: "gb10", model: "" }, b: { host: "gb10", model: "" },
    prompt: "Our 40-person company wants to adopt AI, but our customer data can't leave the building. Outline a pragmatic 90-day adoption plan, including the two biggest risks." },
  { label: "The specialist trap", roles: ["fast", "workhorse"],
    a: { host: "gb10", model: "" }, b: { host: "gb10", model: "" },
    prompt: "Write a short, warm apology email to a customer whose order has now arrived damaged twice." },
  { label: "The trick question", roles: ["fast", "reasoner"],
    a: { host: "gb10", model: "" }, b: { host: "gb10", model: "" },
    prompt: "Summarize the meeting we had yesterday about the Henderson account." },
];

const AR_ROLE_FALLBACKS = {
  fast: ["gemma4:e4b", "qwen3.5:2b", "lfm3:1.7b", "qwen2.5-coder:7b"],
  workhorse: ["gemma4:12b", "nemotron-3.5-lightning:30b", "qwen3.8:27b", "gemma4:e4b"],
  reasoner: ["qwen3.8:27b", "qwen3.5:4b", "gemma4:12b", "qwen3.5:9b", "gemma4:e4b"],
};

let arHosts = [];
const arPowerCharts = {};

const arHost = (key) => arHosts.find((h) => h.key === key);
function arRoleModel(role, hostKey, avoid = "") {
  const models = arHost(hostKey)?.models || [];
  const installed = new Set(models.map((m) => m.name));
  const candidates = [roleModel(role), ...(AR_ROLE_FALLBACKS[role] || [])];
  return candidates.find((name) => name && name !== avoid && installed.has(name)) ||
    models.find((m) => m.name !== avoid)?.name || "";
}

/* 4096 → "4k", 131072 → "128k", 262144 → "262k", 1048576 → "1M" — past
   128k, model cards quote decimal thousands (Qwen's "262k"), so match them */
const fmtCtx = (n) => (!n ? "" : n >= 1048576 && n % 1048576 === 0 ? `${n / 1048576}M`
  : `${Math.round(n / (n > 131072 ? 1000 : 1024))}k`);

/* ---- loadout: how each pane's model is loaded (context, thinking) ---- */
const AR_CTX_CHIPS = ["auto", 4096, 32768, 131072, "max"];
const AR_THINK_CHIPS = [["auto", "Auto"], [true, "On"], [false, "Off"]];
const AR_TEMP_CHIPS = [["auto", "Auto"], [0, "Precise"], [1.2, "Creative"]];
const arLoadout = {
  A: { ctx: "auto", think: "auto", temp: "auto" },
  B: { ctx: "auto", think: "auto", temp: "auto" },
};
const arInfo = { A: null, B: null };   // /api/models/info for the pane's current pick
const arInfoCache = {};                 // host|model → info (specs don't change)
const arInfoSeq = { A: 0, B: 0 };       // drops responses for a pick that's since changed

// The num_ctx a run sends — null means "host default" (don't send one).
function arNumCtx(side) {
  const { ctx } = arLoadout[side];
  const max = arInfo[side]?.max_context;
  if (ctx === "max") return max || null;
  if (typeof ctx === "number") return max ? Math.min(ctx, max) : ctx;
  return null;
}

// The context the model will actually sit at — what staging must match.
const arEffCtx = (side) => arNumCtx(side) || arInfo[side]?.default_context || null;

function arRenderLoadout(side) {
  const info = arInfo[side];
  const lo = arLoadout[side];
  const max = info?.max_context;
  // a fixed size at or past the model's limit is just "Max"
  if (max && typeof lo.ctx === "number" && lo.ctx >= max) lo.ctx = "max";
  const chip = (group, val, label, active) =>
    `<button type="button" class="ar-chip${active ? " active" : ""}" data-group="${group}" data-val="${val}">${label}</button>`;
  $(`arCtx${side}`).innerHTML = AR_CTX_CHIPS.map((c) => {
    if (c === "auto") {
      const d = info?.default_context;
      return chip("ctx", c, d ? `Auto · ${fmtCtx(d)}` : "Auto", lo.ctx === c);
    }
    if (c === "max") return max ? chip("ctx", c, `Max · ${fmtCtx(max)}`, lo.ctx === c) : "";
    return max && c >= max ? "" : chip("ctx", c, fmtCtx(c), lo.ctx === c);
  }).join("");
  const thinks = !!info?.capabilities?.includes("thinking");
  $(`arThinkGroup${side}`).hidden = !thinks;
  if (info && !thinks) lo.think = "auto"; // not before the spec arrives — a preset may have set it
  $(`arThink${side}`).innerHTML = AR_THINK_CHIPS
    .map(([v, label]) => chip("think", v, label, lo.think === v)).join("");
  $(`arTemp${side}`).innerHTML = AR_TEMP_CHIPS
    .map(([v, label]) => chip("temp", v, label, lo.temp === v)).join("");
}

// What each knob means, in one line a customer can take in. Shown under the
// row for whichever group was last hovered or clicked.
const AR_HELP = {
  ctx: "Context is how much the model can hold in mind at once — your prompt plus its answer. Ollama reserves the whole window in memory when it loads, used or not.",
  think: "Thinking lets the model reason to itself before answering. Better on hard questions, slower on easy ones.",
  temp: "Temperature is creativity vs consistency. Precise gives the same answer every time; Creative explores wording and ideas.",
};

function arShowHelp(side, key) {
  $(`arHint${side}`).textContent = AR_HELP[key] || "";
}

function arOnChip(side, e) {
  const b = e.target.closest(".ar-chip");
  if (!b) return;
  const raw = b.dataset.val;
  const val = raw === "auto" || raw === "max" ? raw
    : raw === "true" ? true : raw === "false" ? false : +raw;
  arLoadout[side][b.dataset.group] = val;
  arShowHelp(side, b.dataset.group);
  arRenderLoadout(side);
}

/* live power sampling per pane — the wattage half of the comparison */
function arStartPower(side, host) {
  const chart = arPowerCharts[side];
  chart.data.labels = [];
  chart.data.datasets[0].data = [];
  chart.update("none");
  const st = { n: 0, sumW: 0, wh: 0, last: Date.now(), unavailable: false, timer: null };
  st.timer = setInterval(async () => {
    let watts;
    try {
      ({ watts } = await apiJSON(`/api/power/${host}`));
    } catch {
      return;
    }
    if (watts == null) {
      st.unavailable = true;
      $(`arPowerCap${side}`).textContent = host === "gb10"
        ? "power data unavailable"
        : "power n/a — authorize SSH to this machine to enable wattage";
      return;
    }
    const now = Date.now();
    st.wh += watts * ((now - st.last) / 1000) / 3600;
    st.last = now;
    st.n++;
    st.sumW += watts;
    pushPoint(chart, "", watts, 90);
    chart.update("none");
    $(`arPowerCap${side}`).innerHTML =
      `<b>${Math.round(watts)} W</b> now · avg ${Math.round(st.sumW / st.n)} W · ${st.wh.toFixed(2)} Wh`;
  }, 1000);
  return st;
}

function arStopPower(side, st) {
  clearInterval(st.timer);
  if (!st.n || st.unavailable) return;
  const v = $(`arStats${side}`).querySelectorAll(".v");
  const tokens = (+v[3].textContent || 0) + (+v[4].textContent || 0);
  const perWh = st.wh > 0.0001 ? Math.round(tokens / st.wh) : null;
  $(`arPowerCap${side}`).innerHTML =
    `avg <b>${Math.round(st.sumW / st.n)} W</b> · ${st.wh.toFixed(2)} Wh for this answer` +
    (perWh ? ` · <b>${perWh.toLocaleString()} tokens/Wh</b>` : "");
}

function arFillModels(side, keepModel, loadout) {
  const host = arHost($(`arHost${side}`).value);
  const models = host?.models || [];
  $(`arModel${side}`).innerHTML = models
    .map((m) => `<option value="${m.name}">${m.label}</option>`).join("");
  if (keepModel && models.some((m) => m.name === keepModel)) {
    $(`arModel${side}`).value = keepModel;
  }
  arPickChanged(side, loadout);
}

// New host/model: loadout back to Auto (unless a preset supplies one) and
// fetch the model's real spec from Ollama.
async function arPickChanged(side, loadout) {
  arLoadout[side] = {
    ctx: loadout?.ctx ?? "auto", think: loadout?.think ?? "auto", temp: loadout?.temp ?? "auto",
  };
  const host = $(`arHost${side}`).value, name = $(`arModel${side}`).value;
  const key = `${host}|${name}`;
  const seq = ++arInfoSeq[side];
  arInfo[side] = arInfoCache[key] || null;
  arShowTraits(side);
  arRenderLoadout(side);
  if (arInfo[side] || !name) return;
  try {
    const info = await apiJSON(`/api/models/info?host=${encodeURIComponent(host)}&name=${encodeURIComponent(name)}`);
    arInfoCache[key] = info;
    if (seq !== arInfoSeq[side]) return;
    arInfo[side] = info;
    arShowTraits(side);
    arRenderLoadout(side);
  } catch {
    // the catalog traits line stays up — good enough
  }
}

async function loadArena() {
  arHosts = await apiJSON("/api/hosts");
  const primary = arHost("gb10");
  // Bind presets to models actually installed in this deployment.
  const remote = arHosts.find((h) => h.key !== "gb10" && h.online);
  for (const p of AR_PRESETS.filter((x) => x.crossMachine)) {
    const shared = primary?.models.find((m) => remote?.models.some((r) => r.name === m.name));
    p.a = { host: primary?.key || "", model: shared?.name || "" };
    p.b = { host: remote?.key || "", model: shared?.name || "" };
  }
  for (const p of AR_PRESETS.filter((x) => x.role)) {
    p.a = { ...p.a, host: primary?.key || "", model: arRoleModel(p.role, primary?.key) };
    p.b = { ...p.b, host: primary?.key || "", model: p.a.model };
  }
  for (const p of AR_PRESETS.filter((x) => x.roles)) {
    p.a = { ...p.a, host: primary?.key || "", model: arRoleModel(p.roles[0], primary?.key) };
    p.b = {
      ...p.b, host: primary?.key || "",
      model: arRoleModel(p.roles[1], primary?.key, p.a.model),
    };
  }
  const hostOpts = arHosts
    .map((h) => `<option value="${h.key}" ${h.online ? "" : "disabled"}>${h.label}${h.online ? "" : " (offline)"}</option>`)
    .join("");
  for (const side of ["A", "B"]) {
    $(`arHost${side}`).innerHTML = hostOpts;
    $(`arHost${side}`).value = "gb10";
    $(`arHost${side}`).addEventListener("change", () => arFillModels(side));
    $(`arModel${side}`).addEventListener("change", () => arPickChanged(side));
    $(`arLoadout${side}`).addEventListener("click", (e) => arOnChip(side, e));
    $(`arLoadout${side}`).addEventListener("mouseover", (e) => {
      const g = e.target.closest(".ar-lo-group");
      if (g) arShowHelp(side, g.dataset.help);
    });
    if (!arPowerCharts[side]) arPowerCharts[side] = makeSpark(`arPower${side}`, COLORS.pwr);
  }
  arFillModels("A", arRoleModel("reasoner", "gb10"));
  arFillModels("B", arRoleModel("fast", "gb10"));
  const has = (ref) => {
    const h = arHost(ref.host);
    return h?.online && h.models.some((m) => m.name === ref.model);
  };
  $("arPresets").innerHTML = AR_PRESETS.map((p, i) => {
    const ok = has(p.a) && has(p.b);
    return `<button type="button" class="suggestion" data-preset="${i}" ${ok ? "" : 'disabled title="machine offline or model not installed"'}>${p.label}</button>`;
  }).join("");
  document.querySelectorAll("[data-preset]").forEach((b) =>
    b.addEventListener("click", () => {
      const p = AR_PRESETS[+b.dataset.preset];
      $("arHostA").value = p.a.host;
      $("arHostB").value = p.b.host;
      arFillModels("A", p.a.model, p.a);
      arFillModels("B", p.b.model, p.b);
      $("arPrompt").value = p.prompt;
    }));
}

// One dim line: the model's real spec from its model file, falling back to
// the catalog traits while that loads (or if the host can't say).
function arShowTraits(side) {
  const host = arHost($(`arHost${side}`).value);
  const m = host?.models.find((x) => x.name === $(`arModel${side}`).value);
  const info = arInfo[side];
  const where = host?.label.split(" — ")[0];
  if (!m) { $(`arTraits${side}`).textContent = ""; return; }
  if (!info) {
    $(`arTraits${side}`).textContent = [host.label, m.traits, `${m.size_gb} GB`].filter(Boolean).join(" · ");
    return;
  }
  // the file's own size label ("70B") reads better than a rounded count ("71B")
  const params = /^\d+(\.\d+)?B$/i.test(info.size_label || "") ? info.size_label : info.params_label;
  const caps = ["vision", "audio", "thinking"].filter((c) => info.capabilities?.includes(c));
  $(`arTraits${side}`).textContent = [
    info.moe ? "MoE" : "Dense",
    info.moe && `${info.moe.used} of ${info.moe.experts} experts per token`,
    params,
    info.quant_bits ? `${info.quant_bits}-bit` : info.quant,
    info.max_context && `up to ${fmtCtx(info.max_context)} context`,
    ...caps,
    `${Math.round(m.size_gb)} GB on disk`,
    where,
  ].filter(Boolean).join(" · ");
}

// Stage a local model at the context the run will use. A copy resident at
// another context doesn't count: Ollama would reload it silently mid-run.
async function arEnsureLoaded(name, label, options) {
  const want = options?.num_ctx;
  const ready = (m) => m?.loaded && (!want || !m.context || m.context === want);
  if (ready((await apiJSON("/api/models/status")).find((m) => m.name === name))) return;
  const at = want ? ` at a ${fmtCtx(want)} context` : "";
  $("arStatus").textContent = `Staging ${label} into memory${at}…`;
  await apiJSON("/api/models/load", { name, options });
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const s = (await apiJSON("/api/models/status")).find((m) => m.name === name);
    if (s?.error) throw new Error(s.error);
    if (s?.loaded && !s.busy) return;
    $("arStatus").textContent = `Staging ${label} into memory${at}… ${(i + 1) * 2}s`;
  }
  throw new Error(`${label} took too long to load`);
}

// panes whose model ended up partly on the CPU in the current run
let arSpilled = [];

/* ---- memory bar: weights vs the context reserve, one scale for both panes ---- */
const arMem = { A: null, B: null };
// one decimal, so the caption's parts add up to its total
const fmtGb = (n) => (Math.round(n * 10) / 10).toFixed(1);

function arRenderMemBars() {
  const done = ["A", "B"].filter((s) => arMem[s]);
  const top = Math.max(...done.map((s) => arMem[s].memory_gb), 0);
  ["A", "B"].forEach((side) => {
    const el = $(`arMem${side}`), m = arMem[side];
    if (!m || !top) { el.hidden = true; return; }
    el.hidden = false;
    const bar = el.querySelector(".ar-mb-bar");
    const w = el.querySelector(".ar-mb-w"), r = el.querySelector(".ar-mb-r");
    const used = el.querySelector(".ar-mb-used");
    bar.style.width = `${(100 * m.memory_gb / top).toFixed(2)}%`;
    const known = m.weights_gb != null;
    const reserve = known ? Math.max(m.memory_gb - m.weights_gb, 0) : 0;
    const wFrac = known ? Math.min(m.weights_gb / m.memory_gb, 1) : 1;
    w.style.flex = `0 0 ${(100 * wFrac).toFixed(2)}%`;
    r.hidden = !known || reserve <= 0;
    r.style.flex = "1 1 0";
    bar.classList.toggle("ar-mb-solid", r.hidden);
    used.style.width = m.context ? `${Math.min(100, 100 * m.used / m.context).toFixed(3)}%` : "0";
    const ctx = fmtCtx(m.context);
    r.querySelector("span").textContent = "context reserve";
    const total = `${fmtGb(m.memory_gb)} GB in memory`;
    el.querySelector(".ar-mb-cap").textContent = !known ? `${total} · context reserve unknown`
      : [total, `${fmtGb(m.weights_gb)} GB model`,
        `${fmtGb(reserve)} GB reserved for context`,
        m.context ? `${m.used.toLocaleString()} of ${ctx} tokens used ` +
          `(${(100 * m.used / m.context).toFixed(m.used / m.context < 0.001 ? 2 : 1)}%)` : ""]
        .filter(Boolean).join(" · ");
  });
  // inner words only where they fit — the caption carries the numbers
  requestAnimationFrame(() => ["A", "B"].forEach((side) => {
    $(`arMem${side}`).querySelectorAll(".ar-mb-w, .ar-mb-r").forEach((seg) => {
      const need = seg.classList.contains("ar-mb-w") ? 70 : 110;
      seg.classList.toggle("ar-mb-narrow", seg.getBoundingClientRect().width < need);
    });
  }));
}

async function arRunPane(side, host, model, prompt, signal) {
  const label = $(`arModel${side}`).selectedOptions[0]?.textContent || model;
  const out = $(`arOut${side}`);
  const v = $(`arStats${side}`).querySelectorAll(".v");
  // v: 0 first token · 1 speed · 2 total time · 3 reasoning tokens · 4 answer tokens
  //    5 cloud cost · 6 memory · 7 context
  const target = streamTarget(out);
  target.setError("running…");
  v[6].textContent = "–";
  v[6].classList.remove("ar-spill");
  v[7].textContent = "–";
  arMem[side] = null;
  arRenderMemBars();
  const t0 = Date.now();
  let first = null, nThink = 0, nAnswer = 0;
  const render = () => {
    const total = nThink + nAnswer;
    v[0].textContent = first ? `${((first - t0) / 1000).toFixed(1)}s` : "–";
    v[1].textContent = first
      ? `${(total / Math.max((Date.now() - first) / 1000, 0.5)).toFixed(0)} tok/s` : "–";
    v[2].textContent = `${((Date.now() - t0) / 1000).toFixed(0)}s`;
    v[3].textContent = nThink || (first ? "0" : "–");
    v[4].textContent = nAnswer || (first ? "0" : "–");
    v[5].textContent = `$${(total / 1e6 * 15).toFixed(4)}`;
  };
  const body = { model, host, messages: [{ role: "user", content: prompt }] };
  const numCtx = arNumCtx(side);
  const options = {};
  if (numCtx) options.num_ctx = numCtx;
  // a sampling option — no reload, so staging never looks at it
  if (arLoadout[side].temp !== "auto") options.temperature = arLoadout[side].temp;
  if (Object.keys(options).length) body.options = options;
  if (arLoadout[side].think !== "auto") body.think = arLoadout[side].think;
  let streamError = null;
  await streamSSE("/api/chat", body, {
    message: (d) => {
      if (d.think) { if (!first) first = Date.now(); nThink++; }
      if (d.token) { if (!first) first = Date.now(); nAnswer++; }
      target.onData(d);
      out.scrollTop = out.scrollHeight; // long answers keep streaming in view
      render();
    },
    done: (s) => {
      render();
      v[0].textContent = `${s.time_to_first_token}s`;
      v[1].textContent = `${s.tok_per_sec ?? "?"} tok/s`;
      v[2].textContent = `${s.total_seconds}s`;
      v[5].textContent = `$${((s.tokens ?? nThink + nAnswer) / 1e6 * 15).toFixed(4)}`;
      if (s.loaded) {
        // part of the model spilled off the GPU — the number that explains a crawl
        // "~" = Ollama's own estimate, which runs low for some models here;
        // unmarked = measured as the drop in free memory when it loaded
        const pct = s.loaded.gpu_pct;
        v[6].innerHTML = `${s.loaded.measured ? "" : "~"}${s.loaded.memory_gb} GB` +
          (pct == null ? "" : `<span class="ar-gpu">${pct < 100 ? `${100 - pct}% on CPU` : "100% on GPU"}</span>`);
        v[6].classList.toggle("ar-spill", pct != null && pct < 100);
        if (pct != null && pct < 100) arSpilled.push(label);
        v[7].textContent = fmtCtx(s.loaded.context) || "–";
        if (s.loaded.memory_gb) {
          arMem[side] = {
            memory_gb: s.loaded.memory_gb, weights_gb: s.loaded.weights_gb ?? null,
            context: s.loaded.context || 0,
            used: (s.prompt_tokens || 0) + (s.tokens ?? nThink + nAnswer),
          };
          arRenderMemBars();
        }
      }
      target.finish(null);
    },
    error: (d) => {
      streamError = d.error || "Model request failed";
      target.setError(`Error: ${streamError}`);
    },
  }, signal);
  if (streamError) throw new Error(streamError);
}

const arAbort = { A: null, B: null };

function arRefreshButtons() {
  const busy = !!(arAbort.A || arAbort.B);
  ["arRunA", "arRunB", "arGoBoth"].forEach((id) => { $(id).disabled = busy; });
  $("arStop").hidden = !busy;
}

const AR_SAME_MODEL = "Same model, two loadouts — running one after the other (a model lives in memory one way at a time).";

async function arRun(sides) {
  const prompt = $("arPrompt").value.trim();
  if (!prompt) { $("arStatus").textContent = "Write a prompt (or pick a comparison above) first."; return; }
  if (arAbort.A || arAbort.B) return;
  const picks = Object.fromEntries(sides.map((s) => [s, {
    host: $(`arHost${s}`).value, model: $(`arModel${s}`).value,
  }]));
  // what the context reserves up front counts against memory too
  const size = (s) => (arHost(picks[s].host)?.models.find((m) => m.name === picks[s].model)?.size_gb || 0) +
    (arInfo[s]?.kv_bytes_per_token || 0) * (arEffCtx(s) || 0) / 2 ** 30;
  // local staging loads at the effective context, so an Auto run that
  // follows (no num_ctx sent) finds the model exactly as it will use it
  const stageOpts = (s) => (arEffCtx(s) ? { num_ctx: arEffCtx(s) } : undefined);
  const sequential = sides.length === 2 &&
    picks.A.host === picks.B.host && picks.A.model === picks.B.model;
  if (sequential) {
    const same = arEffCtx("A") === arEffCtx("B") && arLoadout.A.think === arLoadout.B.think &&
      arLoadout.A.temp === arLoadout.B.temp;
    if (same) {
      $("arStatus").textContent = "Pick two different models (or the same model on two machines).";
      return;
    }
  } else if (sides.length === 2) {
    const bothLocal = picks.A.host === "gb10" && picks.B.host === "gb10";
    if (bothLocal && size("A") + size("B") + 18 > (window.__memTotal || 121)) {
      $("arStatus").textContent = "Those two models don't fit in memory together — pick a smaller pairing.";
      return;
    }
  }
  sides.forEach((s) => { arAbort[s] = new AbortController(); });
  arSpilled = [];
  arRefreshButtons();
  // power sampling brackets each pane's own run
  const runOne = async (s) => {
    const mon = arStartPower(s, picks[s].host);
    try {
      await arRunPane(s, picks[s].host, picks[s].model, prompt, arAbort[s].signal);
    } finally {
      arStopPower(s, mon);
    }
  };
  const failures = [];
  try {
    if (sequential) {
      $("arOutB").textContent = "Waits for A to finish…";
      for (const s of sides) {
        if (arAbort[s].signal.aborted) break;
        if (picks[s].host === "gb10") await arEnsureLoaded(picks[s].model, picks[s].model, stageOpts(s));
        $("arStatus").textContent = AR_SAME_MODEL;
        try {
          await runOne(s);
        } catch (err) {
          failures.push(`${picks[s].model}: ${err.message}`);
        }
      }
    } else {
      for (const s of sides) {
        // pre-stage local models so the guard can't evict the other pane's pick;
        // remote boxes load on demand (the pane narrates it)
        if (picks[s].host === "gb10") await arEnsureLoaded(picks[s].model, picks[s].model, stageOpts(s));
      }
      const cross = sides.length === 2 && picks.A.host !== picks.B.host;
      $("arStatus").textContent = sides.length === 2
        ? (cross
          ? "Running on both machines at once — each has its own silicon, no shared bandwidth."
          : "Running both at once — note they share this GPU's memory bandwidth, so each runs slower than solo.")
        : `Running on ${arHost(picks[sides[0]].host)?.label || picks[sides[0]].host}…`;
      const results = await Promise.allSettled(sides.map(runOne));
      results.forEach((result, i) => {
        if (result.status === "rejected") {
          failures.push(`${picks[sides[i]].model}: ${result.reason?.message || result.reason}`);
        }
      });
    }
    $("arStatus").textContent = sides.some((s) => arAbort[s].signal.aborted)
      ? "Stopped."
      : failures.length
        ? `Run failed: ${failures.join("; ")}`
        : "Done — compare the numbers above each answer." +
          [...new Set(arSpilled)].map((l) => ` Part of ${l} spilled to the CPU — that is what slow looks like.`).join("");
  } catch (err) {
    $("arStatus").textContent = `Error: ${err.message}`;
  } finally {
    sides.forEach((s) => { arAbort[s] = null; });
    arRefreshButtons();
  }
}

$("arRunA").addEventListener("click", () => arRun(["A"]));
$("arRunB").addEventListener("click", () => arRun(["B"]));
$("arGoBoth").addEventListener("click", () => arRun(["A", "B"]));
$("arForm").addEventListener("submit", (e) => { e.preventDefault(); arRun(["A", "B"]); });
$("arStop").addEventListener("click", () => { arAbort.A?.abort(); arAbort.B?.abort(); });

/* ============================================================
   Ladder — the second mode of this page, staged like a slide
   deck rather than a dashboard.

   The governing rule, applied to every frame: three sizes only.
   One huge line (five words, legible across a room), one
   supporting line, and everything else is dim texture or behind
   a click. The models' streaming output is set dressing — it
   proves the box is working, it is not there to be read. The
   content is the verdict, and every verdict is stamped by a
   checker on the box, never read out of the model's own answer.
   ============================================================ */
const LD_SIDES = { small: "Small", big: "Big" };
const LD_BEATS = ["ldIdle", "ldPrep", "ldRungBeat", "ldFinale", "ldErr"];
const LD_SYM = { pass: "✓", fail: "✗", error: "!" };

/* The server sends rung_done, rung_start and the first delta of the next rung
   inside the same millisecond, so a stage driven straight off the wire would
   wipe the big model's verdict one frame after it landed — the audience would
   never see the ✓ they waited thirty seconds for. Events are queued instead,
   and the queue holds at a rung boundary long enough for both verdicts to sit
   on screen together and flow into the score strip. */
const LD_HOLD_MS = 2200;
const LD_HOLD_FINALE_MS = 2800;

const LD = {
  info: null, abort: null, done: false,
  of: 4, rung: 0,
  text: { small: "", big: "" },
  live: null, ticker: null,          // the count-up clock on the running pane
  prep: { peak: 0, model: null },    // GB walked in so far (MemAvailable jitters)
  runs: {},                          // rung n -> everything the drawer needs
  work: null,                        // which rung the drawer is showing
  queue: [], hold: 0, holdTimer: null, draining: false, failed: false,
};

const ldEl = (base, side) => $(`ld${base}${LD_SIDES[side]}`);

/* "gemma4:e4b" -> "e4b", "qwen3.5:122b-a10b" -> "122B": the score strip has
   room for a nickname, not a model id. */
function ldShortName(name) {
  const tag = String(name || "").split(":")[1] || String(name || "");
  const head = tag.split("-")[0];
  return /^\d/.test(head) ? head.toUpperCase() : head;
}

function ldBeat(id) {
  for (const b of LD_BEATS) $(b).hidden = b !== id;
}

/* replay an entrance animation on an element that is already on screen */
function ldSwap(el) {
  el.classList.remove("ld-swap");
  void el.offsetWidth;
  el.classList.add("ld-swap");
}

/* Display-only tidy-up: both models wrap rung-4 arithmetic in LaTeX, and raw
   `$18,450 \times 0.62$` on a demo screen just reads as broken. Unwraps inline
   math while keeping escaped dollar signs, which are the whole point of that
   rung's numbers. Never applied before a check — the checkers see the raw
   text, exactly as they did in the audition. */
const LD_DOLLAR = "\u0001"; // parks escaped currency out of the delimiter's way

function ldPretty(text) {
  return text.replace(/\\\$/g, LD_DOLLAR)
    // unwrap $…$ only where the span really is LaTeX, so a plain currency
    // figure sitting next to some math never loses its dollar sign
    .replace(/\$([^$\n]*)\$/g, (m, inner) => (/[\\\u0001]/.test(inner) ? inner : m))
    .replace(/\\times/g, "×").replace(/\\cdot/g, "·")
    .replace(/\\le\b/g, "≤").replace(/\\ge\b/g, "≥")
    .replace(/\\%/g, "%")
    .split(LD_DOLLAR).join("$");
}

/* ---- the panes ------------------------------------------------------- */

function ldPaneState(side, state) {
  $(`ldPane${LD_SIDES[side]}`).dataset.state = state;
}

function ldResetPane(side, state) {
  LD.text[side] = "";
  ldEl("Out", side).innerHTML = "";
  ldEl("Slam", side).hidden = true;
  ldEl("Timer", side).textContent = "0.0s";
  ldEl("Tok", side).textContent = "";
  ldPaneState(side, state);
}

/* A pane that is streaming must never look frozen: rung 4's big model takes
   half a minute. The clock ticks in tenths, the token count rises, the texture
   scrolls. Server-stamped seconds overwrite this the moment the verdict lands. */
function ldStartClock(side) {
  LD.live = { side, t0: performance.now() };
  ldEl("Timer", side).textContent = "0.0s";
  if (!LD.ticker) {
    LD.ticker = setInterval(() => {
      if (!LD.live) return;
      const s = (performance.now() - LD.live.t0) / 1000;
      ldEl("Timer", LD.live.side).textContent = `${s.toFixed(1)}s`;
    }, 80);
  }
}

function ldStopClock() {
  LD.live = null;
  if (LD.ticker) clearInterval(LD.ticker);
  LD.ticker = null;
}

/* ---- the score strip -------------------------------------------------- */

function ldBuildStrip() {
  $("ldTeams").innerHTML = ["small", "big"].map((side) => {
    const m = LD.info.models[side];
    return `<div class="ld-team"><b>${esc(ldShortName(m.name))}</b>`
      + LD.info.rungs.map((r) =>
        `<button type="button" class="ld-mk" id="ldMk-${side}-${r.n}" `
        + `data-rung="${r.n}" title="Rung ${r.n}">·</button>`).join("")
      + "</div>";
  }).join("");
}

function ldMark(side, n, state, label) {
  const el = $(`ldMk-${side}-${n}`);
  if (!el) return;
  const settled = state in LD_SYM;
  el.className = `ld-mk ${state}${settled ? " open pop" : ""}`;
  el.textContent = state === "run" ? "●" : (LD_SYM[state] || "·");
  el.title = label || `Rung ${n}`;
}

/* ---- the verdict slam ------------------------------------------------- */

function ldSlam(side, n, d) {
  const el = ldEl("Slam", side);
  const cls = { pass: "ok", fail: "bad", error: "err" }[d.state] || "err";
  const secs = d.seconds == null ? null : d.seconds.toFixed(1);
  const head = d.state === "pass"
    ? `<div class="hl">${secs ? `${secs}s` : "passed"}</div>`
      + '<div class="sec">every check held</div>'
    : `<div class="hl bad">${esc(d.fail_short
        || (d.state === "error" ? "Not scored" : "Failed"))}</div>`
      + (secs ? `<div class="sec">${secs}s</div>` : "");
  el.innerHTML = `<div class="mk ${cls}">${LD_SYM[d.state] || "!"}</div>${head}`
    + (d.explain ? `<div class="why">${esc(d.explain)}</div>` : "")
    + `<button type="button" class="ld-see" data-rung="${n}">see the work ▾</button>`;
  el.hidden = false;   // display:none -> flex restarts the slam animation
}

/* ---- the prep beat: 76 GB physically walking into the pool ------------ */

function ldPrepBeat(d) {
  ldBeat("ldPrep");
  const fill = $("ldPrepFill");
  const gbs = (v) => Math.round(v || 0);
  if (d.phase === "loading" && LD.prep.model !== d.model) {
    LD.prep = { peak: 0, model: d.model };
    fill.style.transition = "none";
    fill.style.width = "0%";
    void fill.offsetWidth;
    fill.style.transition = "";
  }
  if (d.phase === "clearing") {
    $("ldPrepKick").textContent = "Making room";
    $("ldPrepTitle").textContent = "Clearing the memory pool";
    fill.className = "ld-meter-fill indeterminate";
    $("ldPrepSub").textContent = `${gbs(d.size_gb)} GB of model is about to walk into `
      + `${gbs(d.pool_gb)} GB of unified memory.`;
  } else if (d.phase === "loading") {
    /* The server measures real committed memory, but the box commits the whole
       76 GB inside five seconds and then spends fifteen more actually reading
       the weights — a bar driven straight off that measurement fills, then sits
       frozen for the part of the wait that needs the motion most. So the bar is
       a paced estimate (decaying curve, never reaching 100 % before the load
       reports done) with the measurement mixed in, and it is labelled as
       progress rather than as a reading. The seconds and the resident/free
       figures below are the measured ones. */
    const size = d.size_gb || 8;
    const paced = 1 - Math.exp(-(d.elapsed || 0) / Math.max(0.8, size * 0.12));
    const measured = Math.min(1, (d.loaded_gb || 0) / size);
    const frac = Math.max(LD.prep.peak,
      Math.min(0.99, paced * 0.85 + measured * 0.15));
    LD.prep.peak = frac;
    fill.className = "ld-meter-fill";
    fill.style.width = `${frac * 100}%`;
    $("ldPrepKick").textContent = "Loading";
    $("ldPrepTitle").textContent = `${gbs(size)} GB into one memory pool`;
    $("ldPrepSub").textContent = `${Math.round(size * frac)} GB of ${gbs(size)} `
      + `· ${Math.round(d.elapsed)}s · nothing downloads, nothing leaves`;
  } else if (d.phase === "resident") {
    LD.prep = { peak: 0, model: null };
    fill.className = "ld-meter-fill";
    fill.style.width = "100%";
    $("ldPrepKick").textContent = "Resident";
    $("ldPrepTitle").textContent = d.elapsed >= 1
      ? `${gbs(d.size_gb)} GB in ${Math.round(d.elapsed)} seconds`
      : `${gbs(d.size_gb)} GB already resident`;
    $("ldPrepSub").textContent = `${d.model} is in unified memory. `
      + `${gbs(d.free_gb)} GB of the pool still free.`;
  } else if (d.phase === "ready") {
    fill.className = "ld-meter-fill";
    fill.style.width = "100%";
    $("ldPrepKick").textContent = "Both models resident";
    $("ldPrepTitle").textContent = "Two models, one pool";
    $("ldPrepSub").textContent = `${gbs(d.resident_gb)} GB of models loaded at once, `
      + `${gbs(d.free_gb)} GB of the ${gbs(d.pool_gb)} GB pool still free. Climbing.`;
  } else if (d.status) {
    $("ldPrepSub").textContent = d.status;
  }
}

/* ---- the finale ------------------------------------------------------- */

function ldFinaleBeat(d) {
  $("ldFinPunch").textContent = d.punchline || "Fast until it has to be right.";
  $("ldFinPitch").textContent = d.pitch || "";
  const cells = ["<div></div>"];
  for (const r of LD.info.rungs) {
    cells.push(`<div class="ld-fh">${r.n} · ${esc(r.stage_title || r.title)}</div>`);
  }
  for (const side of ["small", "big"]) {
    const m = LD.info.models[side];
    cells.push(`<div class="ld-fr">${esc(m.name)}<span>${m.size_gb} GB</span></div>`);
    for (const r of LD.info.rungs) {
      const v = (LD.runs[r.n] || {})[side] || {};
      const state = v.state || (d.scorecard && d.scorecard[side][r.n - 1]) || "";
      cells.push(`<div class="ld-fm ${state}">${LD_SYM[state] || "·"}`
        + `<span>${v.seconds != null ? `${v.seconds.toFixed(1)}s` : ""}</span></div>`);
    }
  }
  $("ldFinCard").innerHTML = cells.join("");
  LD.summary = d.summary || "";
  ldBeat("ldFinale");
  ldSwap($("ldFinale"));
}

/* ---- the skeptic's path ----------------------------------------------- */

function ldWorkCol(r, side) {
  const v = r[side] || {};
  const m = LD.info.models[side];
  const verdict = { pass: "✓ passed", fail: "✗ failed", error: "! not scored" }[v.state]
    || "— didn't run";
  const meta = [v.seconds != null ? `${v.seconds.toFixed(1)}s` : "",
    v.tokens ? `${v.tokens} tokens` : ""].filter(Boolean).join(" · ");
  return `<div class="ld-work-col">
    <h4>${esc(m.name)} — full answer</h4>
    <div class="ld-work-verdict ${v.state || ""}">${verdict}${meta ? ` · ${meta}` : ""}</div>
    <div class="ld-work-out md-body">${v.text ? mdRender(ldPretty(v.text)) : "<em>nothing yet</em>"}</div>
    ${v.explain ? `<p class="ld-work-why">${esc(v.explain)}</p>` : ""}
  </div>`;
}

function ldRenderWork() {
  const ns = Object.keys(LD.runs).map(Number).sort((a, b) => a - b);
  if (!ns.length) return ldCloseWork();
  if (!LD.runs[LD.work]) LD.work = ns[ns.length - 1];
  $("ldWorkTabs").innerHTML = ns.map((n) =>
    `<button type="button" class="ld-work-tab${n === LD.work ? " active" : ""}" `
    + `data-rung="${n}">Rung ${n} — ${esc(LD.runs[n].title || "")}</button>`).join("");
  const r = LD.runs[LD.work];
  $("ldWorkBody").innerHTML = `<div class="ld-work-grid">
    <div class="ld-work-col">
      <h4>The exact prompt, word for word</h4>
      <pre>${esc(r.prompt || "")}</pre>
      <h4>What the checker verified</h4>
      <p class="ld-work-why">${esc(r.checks || "")}</p>
      <p class="ld-work-note">Every ✓ and ✗ on the stage is stamped by that checker,
        running in Python on this box — never read out of the model's own answer.</p>
      ${LD.summary ? `<p class="ld-work-note">${esc(LD.summary)}</p>` : ""}
    </div>${ldWorkCol(r, "small")}${ldWorkCol(r, "big")}</div>`;
}

function ldOpenWork(n) {
  if (!LD.runs[n]) return;
  LD.work = n;
  $("ldWork").hidden = false;
  ldRenderWork();
  $("ldWork").scrollIntoView({ behavior: "smooth", block: "start" });
}

function ldCloseWork() {
  $("ldWork").hidden = true;
}

/* ---- the stream ------------------------------------------------------- */

function ldFail(msg) {
  ldReset();
  LD.failed = true;
  $("ldErrTitle").textContent = "The climb stopped";
  $("ldErrMsg").textContent = msg;
  ldBeat("ldErr");
  document.querySelectorAll(".ld-mk.run").forEach((el) => {
    el.className = "ld-mk";
    el.textContent = "·";
  });
}

/* the queue: everything waits behind a rung boundary's transition beat */
function ldEvent(d) {
  LD.queue.push(d);
  ldDrain();
}

function ldDrain() {
  if (LD.draining) return;
  LD.draining = true;
  while (LD.queue.length) {
    const d = LD.queue[0];
    const wall = d.type === "rung_start" || d.type === "done";
    const left = LD.hold - performance.now();
    if (wall && left > 0) {
      LD.draining = false;
      clearTimeout(LD.holdTimer);
      LD.holdTimer = setTimeout(ldDrain, left + 30);
      return;
    }
    LD.queue.shift();
    ldApply(d);
  }
  LD.draining = false;
}

function ldReset() {
  LD.queue = [];
  LD.hold = 0;
  clearTimeout(LD.holdTimer);
  LD.holdTimer = null;
  LD.draining = false;
  ldStopClock();
}

function ldApply(d) {
  if (d.type === "status") {
    ldPrepBeat(d);
  } else if (d.type === "rung_start") {
    LD.rung = d.rung;
    LD.of = d.of || LD.info.rungs.length;
    LD.runs[d.rung] = {
      n: d.rung, title: d.stage_title || d.title, prompt: d.prompt || "",
      checks: d.checks || "", small: null, big: null,
    };
    ldBeat("ldRungBeat");
    $("ldRungN").textContent = `Rung ${d.rung} of ${LD.of}`;
    $("ldRungTitle").textContent = d.stage_title || d.title;
    $("ldRungSub").textContent = d.stage_sub || d.desc;
    ldResetPane("small", "waiting");
    ldResetPane("big", "waiting");
    ldSwap($("ldRungBeat"));
  } else if (d.type === "model_start") {
    ldResetPane(d.side, "live");
    ldStartClock(d.side);
    ldMark(d.side, d.rung, "run", `Rung ${d.rung} — running`);
  } else if (d.type === "delta") {
    LD.text[d.side] += d.text;
    const out = ldEl("Out", d.side);
    out.innerHTML = mdRender(ldPretty(LD.text[d.side]));
    out.scrollTop = out.scrollHeight;
    if (d.tok) ldEl("Tok", d.side).textContent = `${d.tok} tok`;
  } else if (d.type === "verdict") {
    ldStopClock();
    ldPaneState(d.side, "done");
    if (d.seconds != null) ldEl("Timer", d.side).textContent = `${d.seconds.toFixed(1)}s`;
    if (d.tokens) ldEl("Tok", d.side).textContent = `${d.tokens} tok`;
    const r = LD.runs[d.rung] || (LD.runs[d.rung] = { n: d.rung });
    r[d.side] = {
      state: d.state, seconds: d.seconds, tokens: d.tokens,
      explain: d.explain || "", short: d.fail_short || "", text: LD.text[d.side],
    };
    ldSlam(d.side, d.rung, d);
    ldMark(d.side, d.rung, d.state,
      `Rung ${d.rung} — ${d.state}${d.fail_short ? `: ${d.fail_short}` : ""}`);
    if (!$("ldWork").hidden) ldRenderWork();
  } else if (d.type === "rung_done") {
    // the transition beat: both verdicts stand, the strip catches up, then the
    // next title comes up. Without this the second verdict lasts one frame.
    LD.hold = performance.now()
      + (d.rung >= LD.of ? LD_HOLD_FINALE_MS : LD_HOLD_MS);
  } else if (d.type === "done") {
    LD.done = true;
    ldStopClock();
    ldFinaleBeat(d);
  } else if (d.type === "error") {
    ldFail(d.error);
  }
}

async function ldRun() {
  if (LD.abort || !LD.info) return;  // a second click during a run is a no-op
  LD.abort = new AbortController();
  ldReset();
  LD.done = false;
  LD.failed = false;
  LD.runs = {};
  LD.work = null;
  LD.summary = "";
  LD.prep = { peak: 0, model: null };
  LD.text = { small: "", big: "" };
  ldCloseWork();
  ldBuildStrip();
  $("ldStrip").hidden = false;
  $("ldStop").hidden = false;
  $("ldRun").disabled = true;
  ldResetPane("small", "idle");
  ldResetPane("big", "idle");
  ldBeat("ldPrep");
  $("ldPrepKick").textContent = "Getting ready";
  $("ldPrepTitle").textContent = "Waking the box";
  $("ldPrepFill").className = "ld-meter-fill indeterminate";
  $("ldPrepSub").textContent = "Asking Ollama what is already in memory…";
  try {
    await streamSSE("/api/ladder/run", {}, { message: ldEvent }, LD.abort.signal);
    // the last events are usually still sitting behind the finale's hold
    await new Promise((resolve) => {
      const settle = () => (LD.queue.length ? setTimeout(settle, 120) : resolve());
      settle();
    });
    // the server's own error event, if any, already said something truer
    if (!LD.done && !LD.failed) ldFail("The stream ended before the last rung.");
  } catch (err) {
    if (err.name !== "AbortError") { if (!LD.failed) ldFail(err.message); }
    else if (!LD.done) { ldReset(); ldBeat("ldIdle"); $("ldStrip").hidden = true; }
  } finally {
    LD.abort = null;
    $("ldRun").disabled = false;
    $("ldStop").hidden = true;
  }
}

async function loadLadder() {
  const info = await apiJSON("/api/ladder/info");
  LD.info = info;
  LD.of = info.rungs.length;
  ldBuildStrip();
  for (const side of ["small", "big"]) {
    const m = info.models[side];
    ldEl("Name", side).textContent = m.name;
    ldEl("Meta", side).textContent = m.installed ? `${m.size_gb} GB` : "not installed";
  }
  const both = ["small", "big"].map((s) => info.models[s]);
  const total = Math.round(both.reduce((a, m) => a + (m.size_gb || 0), 0));
  $("ldIdleSub").textContent = `${both[0].name} and ${both[1].name} — ${total} GB of `
    + `models, both resident in the same ${info.pool_gb || 122} GB pool, climbing four `
    + "tasks that get harder as they go.";
  // never offer a button that can't work — say what to type instead, quietly
  const missing = ["small", "big"].filter((s) => !info.models[s].installed);
  if (!info.ollama_up) {
    $("ldRun").disabled = true;
    $("ldNote").innerHTML = "Ollama isn't answering — check "
      + "the compute node's container, then reload this page.";
  } else if (missing.length) {
    $("ldRun").disabled = true;
    $("ldNote").innerHTML = "One audited pairing, and it isn't fully installed: "
      + missing.map((s) => `<code>${esc(info.models[s].hint)}</code>`).join(" — ");
  } else {
    $("ldRun").disabled = false;
    $("ldNote").textContent = "First run spends about 20 seconds walking the 122B into "
      + "memory. After that it climbs on its own — one click, no driving.";
  }
}

function arSetMode(ladder) {
  $("arFree").hidden = ladder;
  $("ldWrap").hidden = !ladder;
  $("arModeFree").classList.toggle("active", !ladder);
  $("arModeLadder").classList.toggle("active", ladder);
  // the page lede is a fourth size competing with the stage — CSS hides it
  $("page-compare").classList.toggle("ld-on", ladder);
  if (!ladder) LD.abort?.abort();
}

$("arModeFree").addEventListener("click", () => arSetMode(false));
$("arModeLadder").addEventListener("click", () => arSetMode(true));
$("ldRun").addEventListener("click", ldRun);
$("ldRetry").addEventListener("click", ldRun);
$("ldAgain").addEventListener("click", ldRun);
$("ldStop").addEventListener("click", () => LD.abort?.abort());
$("ldWorkOpen").addEventListener("click", () => ldOpenWork(LD.work || LD.of));
$("ldWorkClose").addEventListener("click", ldCloseWork);
$("ldWorkTabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".ld-work-tab");
  if (!tab) return;
  LD.work = Number(tab.dataset.rung);
  ldRenderWork();
});
// one delegated handler covers the score-strip marks and every slam's link
$("ldStage").addEventListener("click", (e) => {
  const target = e.target.closest(".ld-see, .ld-mk.open");
  if (target) ldOpenWork(Number(target.dataset.rung));
});
// leaving the page mid-climb cancels the run rather than orphaning the stream
window.addEventListener("hashchange", () => {
  if (location.hash.replace("#", "") !== "compare") LD.abort?.abort();
});

/* ============================================================
   Case Works — one case, three desks (public-safety workflow)
   ============================================================ */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function loadCasework() {
  const data = await apiJSON("/api/casework/data");
  $("cwNotes").value = data.field_notes;
  $("cwBundle").innerHTML = data.bundle.map((d) => `
    <details class="cw-source"><summary>${esc(d.title)}</summary><p>${esc(d.body)}</p></details>`).join("");
  $("cwIncidents").innerHTML = data.incidents.map((i) => `
    <details class="cw-source"><summary><b>${esc(i.case)}</b> — ${esc(i.title)}</summary><p>${esc(i.summary)}</p></details>`).join("");
}

document.querySelectorAll(".cw-tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".cw-tab").forEach((t) => t.classList.toggle("active", t === tab));
    ["report", "analysis", "patterns"].forEach((s) => { $(`cw-${s}`).hidden = s !== tab.dataset.stage; });
    $("cwResultWrap").hidden = true;
    $("cwProcessing").hidden = true;
  }));

function renderReport(d) {
  const rows = (arr, f) => (arr || []).map(f).join("");
  return `<div class="cw-doc">
    <div class="cw-doc-watermark">DRAFT — PENDING OFFICER REVIEW</div>
    <div class="cw-doc-head">
      <div class="cw-doc-agency">SPRINGFIELD POLICE DEPARTMENT<br><span>INCIDENT REPORT · FICTIONAL TRAINING SAMPLE</span></div>
      <div class="cw-doc-case">CASE № ${esc(d.case_number)}</div>
    </div>
    <div class="cw-doc-fields">
      <div><label>Offense</label>${esc(d.offense_classification)}</div>
      <div><label>Location</label>${esc(d.location)}</div>
      <div><label>Occurred</label>${esc(d.occurred_window)}</div>
      <div><label>Reporting officer</label>${esc(d.reporting_officer || "OFC. A. DEMO #4821")}</div>
    </div>
    <h3>Narrative</h3>
    <p class="cw-doc-narrative">${esc(d.narrative)}</p>
    <h3>Persons</h3>
    <table><tr><th>Name</th><th>Role</th><th>Details</th></tr>
      ${rows(d.persons, (p) => `<tr><td>${esc(p.name)}</td><td>${esc(p.role)}</td><td>${esc(p.details)}</td></tr>`)}</table>
    <h3>Evidence</h3>
    <table><tr><th>Tag</th><th>Item</th><th>Location found</th></tr>
      ${rows(d.evidence, (e) => `<tr><td>${esc(e.tag)}</td><td>${esc(e.item)}</td><td>${esc(e.location_found)}</td></tr>`)}</table>
    <h3>Follow-ups</h3>
    <ul>${rows(d.follow_ups, (f) => `<li>☐ ${esc(f)}</li>`)}</ul>
    ${(d.review_flags || []).length ? `<h3>Officer review required</h3>
    <ul class="cw-doc-flags">${rows(d.review_flags, (f) => `<li>⚠ ${esc(f)}</li>`)}</ul>` : ""}
  </div>`;
}

function renderAnalysis(d) {
  return `<div class="cw-panels">
    ${(d.contradictions || []).length ? `<div class="cw-contradictions">
      ${d.contradictions.map((c) => `<div class="cw-flag">
        <div class="cw-flag-head">⚑ CONTRADICTION</div>
        <div class="cw-flag-desc">${esc(c.description)}</div>
        <div class="cw-flag-quotes">
          <div><b>${esc(c.source_a)}:</b> “${esc(c.quote_a)}”</div>
          <div><b>${esc(c.source_b)}:</b> “${esc(c.quote_b)}”</div>
        </div></div>`).join("")}</div>` : ""}
    <div class="panel"><div class="panel-title">Reconstructed timeline <span class="panel-unit">every entry cites its source</span></div>
      <div class="cw-timeline">
        ${(d.timeline || []).map((t) => `<div class="cw-tl-row">
          <div class="cw-tl-time">${esc(t.time)}</div>
          <div class="cw-tl-dot"></div>
          <div class="cw-tl-body">${esc(t.event)}<span class="cw-tl-src">${esc(t.source)}</span></div>
        </div>`).join("")}
      </div></div>
    <div class="cw-cols">
      <div class="panel"><div class="panel-title">Observations</div>
        <ul class="cw-list">${(d.observations || []).map((o) => `<li>${esc(o)}</li>`).join("")}</ul></div>
      <div class="panel"><div class="panel-title">Open leads</div>
        <ul class="cw-list">${(d.open_leads || []).map((o) => `<li>→ ${esc(o)}</li>`).join("")}</ul></div>
    </div></div>`;
}

function renderPatterns(d) {
  return `<div class="cw-panels">
    ${(d.linked_groups || []).map((g) => `<div class="cw-group">
      <div class="cw-group-head">LIKELY SERIES <span class="cw-group-conf">${esc(g.confidence)} confidence</span></div>
      <div class="cw-group-cases">${g.case_numbers.map((c) => `<span class="cw-case-chip">${esc(c)}</span>`).join("")}</div>
      <div class="cw-group-attrs">${g.shared_attributes.map((a) => `<span class="chip-attr">${esc(a)}</span>`).join("")}</div>
      <p class="cw-group-why">${esc(g.rationale)}</p>
    </div>`).join("")}
    <div class="panel"><div class="panel-title">Recommended actions</div>
      <ul class="cw-list">${(d.recommended_actions || []).map((a) => `<li>☐ ${esc(a)}</li>`).join("")}</ul></div>
  </div>`;
}

const CW_RENDERERS = { report: renderReport, analysis: renderAnalysis, patterns: renderPatterns };
let cwBusy = false;

document.querySelectorAll("[data-cwrun]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    if (cwBusy) return;
    cwBusy = true;
    const stage = btn.dataset.cwrun;
    btn.disabled = true;
    $("cwResultWrap").hidden = true;
    $("cwProcessing").hidden = false;
    $("cwProcStatus").textContent = "Reading the case material…";
    $("cwProcMeta").textContent = "";
    try {
      await streamSSE("/api/casework/run", {
        stage, notes: stage === "report" ? $("cwNotes").value : null,
      }, {
        message: (d) => {
          if (d.type === "status") $("cwProcStatus").textContent = d.status;
          else if (d.type === "progress") {
            $("cwProcStatus").textContent = "Drafting…";
            $("cwProcMeta").textContent = `${d.tokens} tokens · ${d.seconds}s · all on this device`;
          } else if (d.type === "result") {
            $("cwResult").innerHTML = CW_RENDERERS[d.stage](d.data);
            $("cwResultWrap").hidden = false;
          } else if (d.type === "done") {
            $("cwResultMeta").textContent =
              `Generated in ${Math.round(d.seconds)}s on this device — ${d.tokens} tokens, zero cloud`;
          } else if (d.type === "error") {
            $("cwProcStatus").textContent = `⚠ ${d.error}`;
          }
        },
      });
    } catch (err) {
      $("cwProcStatus").textContent = `⚠ ${err.message}`;
    } finally {
      cwBusy = false;
      btn.disabled = false;
      if (!$("cwResultWrap").hidden) $("cwProcessing").hidden = true;
    }
  }));

$("cwPrint").addEventListener("click", () => window.print());

/* ============================================================
   Agent Works — the same task run generatively, then agentically
   ============================================================ */
let awAbort = null;
let awTasks = [];
const awRun = { generative: null, stuffed: null, agentic: null };

const awFmt = (n) => n.toLocaleString();
const awMoney = (n) => (n < 10 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);

async function loadAgentWorks() {
  const [tasks, models] = await Promise.all([
    apiJSON("/api/agentworks/tasks"), apiJSON("/api/models"),
  ]);
  awTasks = tasks;
  $("awTask").innerHTML = tasks
    .map((t) => `<option value="${t.key}">${t.label} — ${t.blurb}</option>`).join("");
  const preferred = roleModel("workhorse");
  $("awModel").innerHTML = models
    .map((m) => `<option value="${m.name}" ${m.name === preferred ? "selected" : ""}>${m.label}</option>`)
    .join("");
  awShowTask();
}

async function awShowTask() {
  const task = awTasks.find((t) => t.key === $("awTask").value);
  if (!task) return;
  $("awGoal").textContent = task.goal;
  $("awDomain").textContent = task.domain;
  try {
    const files = await apiJSON(`/api/agentworks/files/${task.key}`);
    $("awFiles").innerHTML = files
      .map((f) => `<span class="aw-file">${f.path}</span>`).join(" ");
    $("awFilesToggle").textContent = $("awFiles").hidden
      ? `show workspace (${files.length} files)` : "hide workspace";
  } catch {
    $("awFiles").textContent = `${task.files} files`;
  }
}

const AW_CARD = { generative: "awCardGen", stuffed: "awCardStuff", agentic: "awCardAgent" };
const AW_NOTE = { generative: "awGenNote", stuffed: "awStNote", agentic: "awAgNote" };

/* Only one mode's output is on screen at a time — three live streams at once
   is unreadable from the back of a room. The scoreboard above keeps the
   comparison visible regardless of which tab is showing. */
function awShowTab(mode) {
  for (const m of ["generative", "stuffed", "agentic"]) {
    $(`awView-${m}`).hidden = m !== mode;
  }
  document.querySelectorAll(".aw-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.mode === mode));
}

function awNow(text, sub) {
  $("awNow").innerHTML = text + (sub ? `<span class="aw-now-cost">${sub}</span>` : "");
}

function awSetRunning(mode) {
  const busy = mode !== null;
  ["awRunGen", "awRunStuff", "awRunAgent"].forEach((id) => { $(id).hidden = busy; });
  $("awStop").hidden = !busy;
  ["awTask", "awModel"].forEach((id) => { $(id).disabled = busy; });
  for (const [m, card] of Object.entries(AW_CARD)) {
    $(card).classList.toggle("running", m === mode);
  }
  document.querySelectorAll(".aw-tab").forEach((t) =>
    t.classList.toggle("running", busy && t.dataset.mode === mode));
}

/* The headline once the agent has run: how much more it burned than the
   cheaper ways of asking, stated against whichever of them have run. */
function awUpdateVersus() {
  const { generative: g, stuffed: s, agentic: a } = awRun;
  const el = $("awVersus");
  if (!a) { el.hidden = true; return; }
  const parts = [];
  if (s?.tokens) parts.push(`<b>${(a.tokens / s.tokens).toFixed(0)}×</b> the pasted-in run`);
  if (g?.tokens) parts.push(`<b>${(a.tokens / g.tokens).toFixed(0)}×</b> the one-shot`);
  el.hidden = !parts.length;
  el.innerHTML = parts.length
    ? `The agent burned ${parts.join(" and ")} — and it's the only one that went and got the data itself.`
    : "";
}

function awUpdateScale() {
  const a = awRun.agentic;
  const runs = parseInt($("awRuns").value, 10);
  const seats = parseInt($("awSeats").value, 10);
  $("awRunsV").textContent = runs;
  $("awSeatsV").textContent = seats;
  if (!a) {
    $("awScaleCloud").textContent = "–";
    $("awScaleYear").textContent = "run the agent first";
    return;
  }
  const monthly = a.cost * runs * seats * 22; // 22 working days
  $("awScaleCloud").textContent = `$${Math.round(monthly).toLocaleString()}`;
  $("awScaleYear").textContent = `$${Math.round(monthly * 12).toLocaleString()}`;
}

/* The one-shot result is the teaching moment, but on its own it just looks
   like the demo broke — so say which of the two failure modes just happened. */
function awGenVerdict(text) {
  const el = $("awGenVerdict");
  const asked = /\b(don't|do not|cannot|can't|unable to)\b[^.]{0,60}\b(access|see|read|open)\b|\b(provide|share|paste|upload|attach)\b[^.]{0,40}\b(file|report|policy|content|document)/i.test(text);
  el.hidden = false;
  el.innerHTML = asked
    ? "<b>That's the honest answer.</b> A generative model only knows what's in its prompt, so it has to ask you to bring it the data. Nothing was audited. Now try button 2."
    : "<b>Check that answer against the files.</b> It couldn't read them, so any specifics in there — names, amounts, versions — were invented. Confident and ungrounded is the failure mode worth showing a customer. Now try button 2.";
  $("awGenNote").textContent = asked ? "refused — asked for the data" : "answered anyway — ungrounded";
  awNow(asked ? "<b>Done.</b> It refused, correctly — it has no way to reach the files."
              : "<b>Done.</b> It answered without the files. Check the specifics against the workspace.",
        "nothing was actually read");
}

function awStepEl(step, reporting) {
  const el = document.createElement("div");
  el.className = "aw-step active";
  el.id = `awStep${step}`;
  el.innerHTML = `<div class="aw-step-head"><b>${reporting ? "Writing the report" : `Step ${step}`}</b>`
    + `<span class="aw-step-cost" id="awStepCost${step}">…</span></div>`;
  $("awTrace").appendChild(el);
  document.querySelectorAll(".aw-step").forEach((s) => {
    if (s !== el) s.classList.remove("active");
  });
  $("awTrace").scrollTop = $("awTrace").scrollHeight;
  return el;
}

function awRenderFindings(findings, label, value) {
  const box = $("awFindings");
  if (!findings.length) {
    box.className = "dim";
    box.textContent = "Nothing recorded yet.";
    return;
  }
  box.className = "";
  const money = findings.some((f) => f.amount != null);
  const rows = findings.map((f) => `<tr><td><b>${f.subject}</b></td><td>${f.issue}`
    + (f.detail ? `<span class="aw-detail">${f.detail}</span>` : "")
    + `</td>${money ? `<td class="num">${f.amount != null ? `$${f.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}</td>` : ""}</tr>`).join("");
  box.innerHTML = `<table class="aw-findings"><thead><tr><th>Record</th><th>Finding</th>`
    + `${money ? '<th class="num">Amount</th>' : ""}</tr></thead><tbody>${rows}</tbody></table>`
    + `<div class="aw-tally"><span class="k">${label}</span><span class="v">${value}</span></div>`;
}

async function awStart(mode) {
  awAbort = new AbortController();
  awSetRunning(mode);
  const agentic = mode === "agentic";
  const findings = [];
  let stepEl = null;
  let sayEl = null;
  let reportText = "";
  let genText = "";
  // The live line has to stay useful during the long stretch where the model
  // is just generating: tools fire at the very end of a step, so without
  // carrying the last completed action forward it reads "thinking" for most
  // of the run.
  let runningCost = "";
  let lastAction = "";
  const subLine = () => [lastAction && `last: ${lastAction}`, runningCost].filter(Boolean).join(" · ");

  const outId = mode === "stuffed" ? "awStOut" : "awGenOut";
  awShowTab(mode);
  awNow("<b>Starting…</b>");
  $(AW_CARD[mode]).classList.remove("done");
  $(AW_NOTE[mode]).textContent = "running";
  if (agentic) {
    $("awTrace").innerHTML = "";
    $("awFindings").className = "dim";
    $("awFindings").textContent = "Nothing recorded yet.";
    $("awReportWrap").hidden = true;
    $("awReport").className = "aw-out md-body";
    $("awReport").textContent = "…";
    $("awTraceStatus").textContent = "starting…";
    ["awAgTokens", "awAgCost", "awAgTime"].forEach((id) => { $(id).textContent = "–"; });
  } else {
    $(outId).className = "aw-out md-body";
    $(outId).textContent = "…";
    $(mode === "stuffed" ? "awStVerdict" : "awGenVerdict").hidden = true;
    (mode === "stuffed"
      ? ["awStTokens", "awStCost", "awStTime"]
      : ["awGenTokens", "awGenCost", "awGenTime"]).forEach((id) => { $(id).textContent = "–"; });
  }

  try {
    await streamSSE("/api/agentworks/run", {
      task: $("awTask").value, model: $("awModel").value, mode,
    }, {
      message: (d) => {
        if (d.type === "status") {
          awNow(`<b>${d.status}</b>`, "a cold model has to be read into memory first");
          if (agentic) $("awTraceStatus").textContent = d.status;
          else $(outId).textContent = d.status;
        } else if (d.type === "start") {
          if (agentic) {
            $("awTraceStatus").textContent = "running";
            awNow("<b>Step 1</b> — deciding what to look at");
          } else if (mode === "stuffed") {
            awNow(`<b>Reading ${d.files_pasted} files in one prompt</b>`,
                  "everything up front, one pass, no follow-up");
          } else {
            awNow("<b>Answering from the prompt alone</b>", "no tools, no files");
          }
        } else if (d.type === "step") {
          stepEl = awStepEl(d.step, d.reporting);
          sayEl = null;
          if (d.reporting) {
            awNow("<b>Writing the report</b>", "from the findings it recorded");
            $("awReportWrap").hidden = false;
            $("awReport").className = "aw-out md-body";
            $("awReport").textContent = "…";
            $("awReportWrap").scrollIntoView({ behavior: "smooth", block: "nearest" });
          } else {
            awNow(`<b>Step ${d.step}</b> — deciding what to do next`, subLine());
          }
        } else if (d.type === "delta") {
          if (!d.text) return;
          if (!agentic) {  // the one-shot answer itself
            genText += d.text;
            $(outId).className = "aw-out md-body";
            $(outId).innerHTML = mdRender(genText);
            return;
          }
          // what the model says between tool calls — dim, it isn't the answer
          if (!sayEl && stepEl) {
            sayEl = document.createElement("div");
            sayEl.className = "aw-say";
            stepEl.appendChild(sayEl);
          }
          if (sayEl) {
            sayEl.textContent += d.text;
            $("awTrace").scrollTop = $("awTrace").scrollHeight;
          }
        } else if (d.type === "report") {
          reportText += d.text || "";
          $("awReport").innerHTML = mdRender(reportText);
        } else if (d.type === "tool") {
          if (!stepEl) return;
          const el = document.createElement("div");
          el.className = `aw-call${d.name === "record_finding" ? " finding" : ""}`;
          const arg = Object.values(d.args || {})[0];
          el.innerHTML = `<code>${d.name}</code><span class="aw-arg">${
            arg === undefined ? "" : String(arg).slice(0, 70)}</span>`;
          el.dataset.pending = "1";
          stepEl.appendChild(el);
          $("awTrace").scrollTop = $("awTrace").scrollHeight;
          awNow(`<b>Step ${d.step}</b> · calling ${d.name} ${arg === undefined ? "" : String(arg).slice(0, 60)}`,
                subLine());
        } else if (d.type === "tool_result") {
          const pending = stepEl?.querySelector('.aw-call[data-pending="1"]');
          if (pending) {
            pending.removeAttribute("data-pending");
            const ret = document.createElement("span");
            ret.className = "aw-ret";
            ret.textContent = `→ ${d.summary}`;
            pending.appendChild(ret);
          }
          lastAction = `${d.name} → ${d.summary}`;
        } else if (d.type === "finding") {
          findings.push(d.finding);
          awRenderFindings(findings, d.tally_label, d.tally_value);
        } else if (d.type === "nudge") {
          const el = document.createElement("div");
          el.className = "aw-nudge";
          el.textContent = "↺ nothing recorded yet — the harness pushed it back to work";
          stepEl?.appendChild(el);
        } else if (d.type === "step_done") {
          const cost = $(`awStepCost${d.step}`);
          if (cost) {
            cost.textContent = `${awFmt(d.step_in)} in · ${awFmt(d.step_out)} out · ${awMoney(d.cost)} so far`;
          }
          $("awAgTokens").textContent = awFmt(d.in_tokens + d.out_tokens);
          $("awAgCost").textContent = awMoney(d.cost);
          $("awAgTime").textContent = `${Math.round(d.seconds)}s`;
          $("awAgNote").textContent = `${d.step} steps · ${d.tool_calls} tool calls`;
          $("awTraceStatus").textContent = `step ${d.step} · ${awFmt(d.in_tokens)} tokens of context replayed so far`;
          runningCost = `this step re-sent ${awFmt(d.step_in)} tokens of context · `
            + `${awFmt(d.in_tokens + d.out_tokens)} tokens and ${awMoney(d.cost)} so far`;
        } else if (d.type === "final") {
          $("awReport").className = "aw-out md-body";
          $("awReport").innerHTML = mdRender(d.text || reportText);
          awRenderFindings(d.findings || [], d.tally_label, d.tally_value);
        } else if (d.type === "done") {
          const tokens = d.in_tokens + d.out_tokens;
          awRun[mode] = { tokens, cost: d.cost, seconds: d.seconds };
          $(AW_CARD[mode]).classList.add("done");
          if (agentic) {
            $("awAgTokens").textContent = awFmt(tokens);
            $("awAgCost").textContent = awMoney(d.cost);
            $("awAgTime").textContent = `${Math.round(d.seconds)}s`;
            $("awAgNote").textContent = `${d.steps} steps · ${d.tool_calls} tool calls · ${d.findings} findings`;
            $("awTraceStatus").textContent =
              `done — ${d.steps} steps, ${d.tool_calls} tool calls, ${awFmt(d.in_tokens)} tokens of context replayed`;
            awNow("<b>Done.</b> It read the files, recorded its findings, and wrote the report below.",
                  `${d.steps} steps · ${d.tool_calls} tool calls · ${awFmt(d.in_tokens)} tokens of context replayed`);
          } else if (mode === "stuffed") {
            $("awStTokens").textContent = awFmt(tokens);
            $("awStCost").textContent = awMoney(d.cost);
            $("awStTime").textContent = `${Math.round(d.seconds)}s`;
            $("awStNote").textContent = `${d.files_pasted} files pasted · one pass`;
            awNow("<b>Done.</b> One prompt in, one answer out — now check it against the agent's findings.",
                  "no re-reading, no cross-checking, no arithmetic it can verify");
            $("awStVerdict").hidden = false;
            $("awStVerdict").innerHTML =
              `<b>It answered — in one pass, from ${d.files_pasted} pasted files.</b> Now audit the auditor: `
              + "does its total match its own rows? Did it catch anything that only shows up across two files? "
              + "Cheap and fast is not the same as thorough — and this only works while the data still fits in a prompt.";
          } else {
            $("awGenTokens").textContent = awFmt(tokens);
            $("awGenCost").textContent = awMoney(d.cost);
            $("awGenTime").textContent = `${Math.round(d.seconds)}s`;
            awGenVerdict(genText);
          }
          awUpdateVersus();
          awUpdateScale();
          if (agentic) $("awScaleBox").open = true;  // the money slide, once there's a number
        } else if (d.type === "error") {
          if (agentic) $("awTraceStatus").textContent = `⚠ ${d.error}`;
          else $(outId).textContent = `⚠ ${d.error}`;
        }
      },
    }, awAbort.signal);
  } catch (err) {
    const msg = err.name === "AbortError" ? "stopped" : `⚠ ${err.message}`;
    if (agentic) $("awTraceStatus").textContent = msg;
    else $(outId).textContent = msg;
  } finally {
    awSetRunning(null);
    awAbort = null;
  }
}

$("awRunGen").addEventListener("click", () => awStart("generative"));
$("awRunStuff").addEventListener("click", () => awStart("stuffed"));
$("awRunAgent").addEventListener("click", () => awStart("agentic"));
$("awStop").addEventListener("click", () => awAbort?.abort());
$("awTask").addEventListener("change", awShowTask);
$("awTabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".aw-tab");
  if (tab) awShowTab(tab.dataset.mode);
});
$("awFilesToggle").addEventListener("click", () => {
  const box = $("awFiles");
  box.hidden = !box.hidden;
  $("awFilesToggle").textContent = box.hidden ? "show workspace" : "hide workspace";
});
["awRuns", "awSeats"].forEach((id) => $(id).addEventListener("input", awUpdateScale));

/* ============================================================
   Batch Works
   ============================================================ */
let bwAbort = null;

async function loadBatchControls() {
  const [jobs, models] = await Promise.all([apiJSON("/api/batch/jobs"), apiJSON("/api/models")]);
  $("bwJob").innerHTML = jobs
    .map((j) => `<option value="${j.key}" title="${j.blurb}">${j.label}</option>`).join("");
  const fast = roleModel("fast") || roleModel("workhorse");
  $("bwModel").innerHTML = models
    .map((m) => `<option value="${m.name}" ${m.name === fast ? "selected" : ""}>${m.label}</option>`)
    .join("");
}

function bwSetRunning(running) {
  $("bwRun").hidden = running;
  $("bwStop").hidden = !running;
  ["bwJob", "bwCount", "bwModel", "bwReason"].forEach((id) => { $(id).disabled = running; });
  $("bwProgress").hidden = !running;
}

$("bwRun").addEventListener("click", async () => {
  const count = parseInt($("bwCount").value, 10);
  bwAbort = new AbortController();
  bwSetRunning(true);
  $("bwStatus").textContent = "starting…";
  $("bwProgressFill").style.width = "0%";
  let outBuf = "";
  let thinkBuf = "";
  const showOut = () => {
    // visible answer once it starts; until then, stream the model's
    // thinking dimmed so long reasoning phases never look stalled
    if (outBuf) {
      $("bwOut").classList.remove("dim");
      $("bwOut").textContent = outBuf.slice(-1500);
    } else if (thinkBuf) {
      $("bwOut").classList.add("dim");
      $("bwOut").textContent = `[thinking] ${thinkBuf.slice(-1400)}`;
    } else {
      $("bwOut").classList.add("dim");
      $("bwOut").textContent = "waiting for the first token…";
    }
  };
  const showTotals = (d) => {
    $("bwTokens").textContent = fmtCount(d.run_in + d.run_out);
    $("bwSaved").textContent = `$${d.saved.toFixed(4)}`;
  };
  try {
    await streamSSE("/api/batch/run", {
      job: $("bwJob").value, count, model: $("bwModel").value,
      reasoning: $("bwReason").value === "on",
    }, {
      message: (d) => {
        if (d.type === "status") {
          $("bwStatus").textContent = d.status;
        } else if (d.type === "start") {
          $("bwStatus").textContent = "running";
        } else if (d.type === "item") {
          $("bwItems").textContent = `${d.index} of ${d.total}`;
          $("bwItem").textContent = d.preview;
          $("bwItem").classList.remove("dim");
          outBuf = "";
          thinkBuf = "";
          showOut();
        } else if (d.type === "tick") {
          outBuf += d.delta;
          thinkBuf += d.think_delta || "";
          showOut();
          showTotals(d);
          $("bwStatus").textContent =
            `item ${d.index}: ${d.phase} · ${d.item_tokens} tokens · ${d.tok_per_sec} tok/s`;
        } else if (d.type === "item_done") {
          outBuf += d.delta;
          showOut();
          showTotals(d);
          $("bwStatus").textContent =
            `item ${d.index} done in ${Math.round(d.seconds)}s (${d.item_tokens} tokens)`;
          $("bwProgressFill").style.width = `${(100 * d.index) / d.total}%`;
        } else if (d.type === "done") {
          showTotals(d);
          $("bwStatus").textContent =
            `done — ${d.items} items in ${Math.round(d.seconds)}s, all on this device`;
        } else if (d.type === "error") {
          $("bwStatus").textContent = `⚠ ${d.error}`;
        }
      },
    }, bwAbort.signal);
  } catch (err) {
    $("bwStatus").textContent = err.name === "AbortError" ? "stopped" : `⚠ ${err.message}`;
  } finally {
    bwSetRunning(false);
    bwAbort = null;
  }
});

$("bwStop").addEventListener("click", () => bwAbort?.abort());

/* ============================================================
   Build-Off — two models build the same thing, results rendered
   ============================================================ */
const BO_SUFFIX = " Respond with ONE complete, self-contained HTML file — all CSS and JS inline, no external resources.";
const BO_PRESETS = [
  { label: "Ad agency site", prompt: "Build me a 3-page website for my ad agency 'Northlight Creative' — Home, Work, and Contact — implemented as one page with tabbed navigation." },
  { label: "Bakery landing page", prompt: "Build a landing page for a neighborhood bakery called 'Flour & Fern' with a menu section and opening hours." },
  { label: "Analytics dashboard", prompt: "Build an analytics dashboard mockup for a coffee-shop chain with a few KPI cards and a simple bar chart drawn in CSS or SVG (invent the data)." },
  { label: "Playable snake", prompt: "Build a playable Snake game with arrow-key controls, a score counter, and a restart button." },
];

let boHosts = [];
const boAbort = { A: null, B: null };
const boCode = { A: "", B: "" };

function boExtractHTML(text) {
  const fences = [...text.matchAll(/```(?:html)?\s*([\s\S]*?)```/gi)].map((m) => m[1]);
  let code = fences.sort((a, b) => b.length - a.length)[0] || "";
  if (!code) {
    const idx = text.search(/<!doctype html|<html/i);
    if (idx >= 0) code = text.slice(idx);
  }
  return code.trim();
}

function boFillModels(side, keep) {
  const host = boHosts.find((h) => h.key === $(`boHost${side}`).value);
  const models = host?.models || [];
  $(`boModel${side}`).innerHTML = models
    .map((m) => `<option value="${m.name}">${m.label}</option>`).join("");
  if (keep && models.some((m) => m.name === keep)) $(`boModel${side}`).value = keep;
  boShowTraits();
}

function boShowTraits() {
  for (const side of ["A", "B"]) {
    const host = boHosts.find((h) => h.key === $(`boHost${side}`).value);
    const m = host?.models.find((x) => x.name === $(`boModel${side}`).value);
    $(`boTraits${side}`).textContent = m
      ? [host.label, m.traits, `${m.size_gb} GB`].filter(Boolean).join(" · ") : "";
  }
}

async function loadBuildoff() {
  boHosts = await apiJSON("/api/hosts");
  const hostOpts = boHosts
    .map((h) => `<option value="${h.key}" ${h.online ? "" : "disabled"}>${h.label}</option>`).join("");
  for (const side of ["A", "B"]) {
    $(`boHost${side}`).innerHTML = hostOpts;
    $(`boHost${side}`).value = "gb10";
    $(`boHost${side}`).addEventListener("change", () => boFillModels(side));
    $(`boModel${side}`).addEventListener("change", boShowTraits);
  }
  boFillModels("A", roleModel("fast") || roleModel("workhorse"));
  boFillModels("B", roleModel("workhorse"));
  $("boPresets").innerHTML = BO_PRESETS.map((p, i) =>
    `<button type="button" class="suggestion" data-bopreset="${i}">${p.label}</button>`).join("");
  document.querySelectorAll("[data-bopreset]").forEach((b) =>
    b.addEventListener("click", () => { $("boPrompt").value = BO_PRESETS[+b.dataset.bopreset].prompt; }));
}

function boRefreshButtons() {
  const busy = !!(boAbort.A || boAbort.B);
  ["boRunA", "boRunB", "boGoBoth"].forEach((id) => { $(id).disabled = busy; });
  $("boStop").hidden = !busy;
}

function boRunPane(side, host, model, prompt, signal) {
  const out = $(`boOut${side}`);
  const v = $(`boStats${side}`).querySelectorAll(".v");
  $(`boResult${side}`).hidden = true;
  boCode[side] = "";
  const target = streamTarget(out);
  target.setError("building…");
  const t0 = Date.now();
  let first = null, nThink = 0, nCode = 0, raw = "";
  const render = () => {
    const total = nThink + nCode;
    v[0].textContent = first ? `${((first - t0) / 1000).toFixed(1)}s` : "–";
    v[1].textContent = first
      ? `${(total / Math.max((Date.now() - first) / 1000, 0.5)).toFixed(0)} tok/s` : "–";
    v[2].textContent = `${((Date.now() - t0) / 1000).toFixed(0)}s`;
    v[3].textContent = nThink || (first ? "0" : "–");
    v[4].textContent = nCode || (first ? "0" : "–");
    v[5].textContent = `$${(total / 1e6 * 15).toFixed(4)}`;
  };
  return streamSSE("/api/chat", { model, host, messages: [{ role: "user", content: prompt + BO_SUFFIX }] }, {
    message: (d) => {
      if (d.think) { if (!first) first = Date.now(); nThink++; }
      if (d.token) { if (!first) first = Date.now(); nCode++; raw += d.token; }
      target.onData(d);
      out.scrollTop = out.scrollHeight;
      render();
    },
    done: (s) => {
      render();
      v[0].textContent = `${s.time_to_first_token}s`;
      v[1].textContent = `${s.tok_per_sec ?? "?"} tok/s`;
      v[2].textContent = `${s.total_seconds}s`;
      target.finish(null);
      const code = boExtractHTML(raw);
      if (code) {
        boCode[side] = code;
        $(`boFrame${side}`).srcdoc = code;
        $(`boResult${side}`).hidden = false;
      } else {
        target.setError("The model didn't produce an HTML file — its raw output is above.");
      }
    },
    error: (d) => target.setError(`Error: ${d.error}`),
  }, signal);
}

async function boRun(sides) {
  const prompt = $("boPrompt").value.trim();
  if (!prompt) { $("boStatus").textContent = "Pick a brief above (or write one) first."; return; }
  if (boAbort.A || boAbort.B) return;
  const picks = Object.fromEntries(sides.map((s) => [s, {
    host: $(`boHost${s}`).value, model: $(`boModel${s}`).value,
  }]));
  const size = (p) => boHosts.find((h) => h.key === p.host)?.models
    .find((m) => m.name === p.model)?.size_gb || 0;
  if (sides.length === 2 && picks.A.host === "gb10" && picks.B.host === "gb10"
      && size(picks.A) + size(picks.B) + 18 > (window.__memTotal || 121)) {
    $("boStatus").textContent = "Those two models don't fit in memory together — pick a smaller pairing.";
    return;
  }
  sides.forEach((s) => { boAbort[s] = new AbortController(); });
  boRefreshButtons();
  try {
    for (const s of sides) {
      if (picks[s].host === "gb10") await arEnsureLoaded(picks[s].model, picks[s].model);
    }
    $("boStatus").textContent = sides.length === 2 ? "Both models building…" : "Building…";
    await Promise.allSettled(sides.map((s) =>
      boRunPane(s, picks[s].host, picks[s].model, prompt, boAbort[s].signal)));
    $("boStatus").textContent = sides.some((s) => boAbort[s].signal.aborted)
      ? "Stopped." : "Done — compare the builds and the numbers.";
  } catch (err) {
    $("boStatus").textContent = `Error: ${err.message}`;
  } finally {
    sides.forEach((s) => { boAbort[s] = null; });
    boRefreshButtons();
  }
}

$("boRunA").addEventListener("click", () => boRun(["A"]));
$("boRunB").addEventListener("click", () => boRun(["B"]));
$("boGoBoth").addEventListener("click", () => boRun(["A", "B"]));
$("boForm").addEventListener("submit", (e) => e.preventDefault());
$("boStop").addEventListener("click", () => { boAbort.A?.abort(); boAbort.B?.abort(); });
["A", "B"].forEach((side) =>
  $(`boOpen${side}`).addEventListener("click", () => {
    if (!boCode[side]) return;
    window.open(URL.createObjectURL(new Blob([boCode[side]], { type: "text/html" })), "_blank");
  }));

/* ============================================================
   Box Bench — auto-graded accuracy battery
   ============================================================ */
let bhAbort = null;

async function loadBench() {
  const [tasks, hosts] = await Promise.all([apiJSON("/api/bench/tasks"), apiJSON("/api/hosts")]);
  $("bhTasks").innerHTML = tasks.map((t) => `
    <details class="bh-task" id="bh-${t.id}">
      <summary><span class="bh-cat">${esc(t.category)}</span> ${esc(t.label)}
        <span class="bh-result" data-result>·</span></summary>
      <p class="bh-why">${esc(t.why)}</p>
      <p><b>Prompt:</b> ${esc(t.prompt)}</p>
      <p class="bh-graded"><b>Graded:</b> ${esc(t.graded)}</p>
    </details>`).join("");
  $("bhHost").innerHTML = hosts
    .map((h) => `<option value="${h.key}" ${h.online ? "" : "disabled"}>${h.label}</option>`).join("");
  const fillModels = () => {
    const h = hosts.find((x) => x.key === $("bhHost").value);
    $("bhModel").innerHTML = (h?.models || [])
      .map((m) => `<option value="${m.name}">${m.label}</option>`).join("");
  };
  $("bhHost").addEventListener("change", fillModels);
  fillModels();
  refreshBenchBoard();
}

async function refreshBenchBoard() {
  let runs;
  try {
    runs = await apiJSON("/api/bench/runs");
  } catch {
    return;
  }
  if (!runs.length) return;
  const rows = runs.slice().reverse().slice(0, 12);
  $("bhBoard").classList.remove("dim");
  $("bhBoard").innerHTML = `<table class="bh-table">
    <tr><th>Model</th><th>Machine</th><th>Reasoning</th><th>Score</th><th>Time</th><th>When</th></tr>
    ${rows.map((r) => `<tr>
      <td>${esc(r.model)}</td><td>${esc(r.host)}</td><td>${esc(r.reasoning)}</td>
      <td class="bh-score">${r.score}/${r.total}</td>
      <td>${Math.round(r.seconds)}s</td><td class="dim">${esc(r.at)}</td>
    </tr>`).join("")}
  </table>`;
}

$("bhRun").addEventListener("click", async () => {
  if (bhAbort) return;
  bhAbort = new AbortController();
  $("bhRun").hidden = true;
  $("bhStop").hidden = false;
  ["bhHost", "bhModel", "bhReason"].forEach((id) => { $(id).disabled = true; });
  $("bhScore").textContent = "0";
  $("bhStatus").textContent = "starting…";
  document.querySelectorAll(".bh-task [data-result]").forEach((el) => {
    el.textContent = "·";
    el.className = "bh-result";
  });
  const t0 = Date.now();
  const timer = setInterval(() => {
    $("bhTime").textContent = `${Math.round((Date.now() - t0) / 1000)}s`;
  }, 1000);
  try {
    await streamSSE("/api/bench/run", {
      host: $("bhHost").value, model: $("bhModel").value, reasoning: $("bhReason").value,
    }, {
      message: (d) => {
        if (d.type === "status") $("bhStatus").textContent = d.status;
        else if (d.type === "task") {
          $("bhTask").textContent = `${d.index} of ${d.total}`;
          $("bhStatus").textContent = `running: ${d.label}`;
          const el = document.querySelector(`#bh-${d.id} [data-result]`);
          if (el) { el.textContent = "…"; el.className = "bh-result bh-running"; }
        } else if (d.type === "task_done") {
          $("bhScore").textContent = `${d.score}`;
          const el = document.querySelector(`#bh-${d.id} [data-result]`);
          if (el) {
            el.textContent = d.passed ? `PASS · ${d.seconds}s` : `FAIL — got: ${d.got}`;
            el.className = `bh-result ${d.passed ? "bh-pass" : "bh-fail"}`;
          }
        } else if (d.type === "done") {
          $("bhScore").textContent = `${d.score}/${d.total}`;
          $("bhStatus").textContent =
            `done — ${d.score}/${d.total} in ${Math.round(d.seconds)}s, graded by code on this device`;
          refreshBenchBoard();
        } else if (d.type === "error") {
          $("bhStatus").textContent = `Error: ${d.error}`;
        }
      },
    }, bhAbort.signal);
  } catch (err) {
    $("bhStatus").textContent = err.name === "AbortError" ? "stopped" : `Error: ${err.message}`;
  } finally {
    clearInterval(timer);
    bhAbort = null;
    $("bhRun").hidden = false;
    $("bhStop").hidden = true;
    ["bhHost", "bhModel", "bhReason"].forEach((id) => { $(id).disabled = false; });
  }
});
$("bhStop").addEventListener("click", () => bhAbort?.abort());

/* ============================================================
   Vertical scenarios: data-driven pages + Scenario Studio
   ============================================================ */
const CTA_FILL = { chat: "chatInput", rag: "ragQuestion", vision: "visionQuestion", doc: "docPrompt" };
let scenarioList = [];

function renderScenarioCta(c) {
  if (c.href) return `<a class="btn uc-cta" href="${esc(c.href)}" target="_blank" rel="noopener">${esc(c.label)}</a>`;
  const fill = CTA_FILL[c.target]
    ? `data-fill-id="${CTA_FILL[c.target]}" data-fill="${esc(c.prompt || "")}"` : "";
  const ragdoc = c.ragdoc ? `data-ragdoc="${esc(c.ragdoc)}"` : "";
  const goto = c.target === "doc" ? "docout" : c.target;
  return `<button class="btn ${c.primary ? "primary" : ""} uc-cta" data-goto="${esc(goto)}" ${fill} ${ragdoc}>${esc(c.label)}</button>`;
}

function renderScenarioPage(s) {
  const tools = s.custom ? `<div class="st-tools dim">Custom scenario ·
      <button class="linklike" data-stedit="${esc(s.id)}">edit</button> ·
      <a class="linklike" href="/api/scenarios/${esc(s.id)}/export">export</a> ·
      <button class="linklike" data-stdelete="${esc(s.id)}">delete</button></div>` : "";
  return `<section class="page" id="page-${esc(s.id)}" hidden>
    <div class="page-head"><h2>${esc(s.title)}</h2><p>${s.tagline}</p>${tools}</div>
    <div class="uc-grid">
      <div class="panel uc-story">
        <div class="panel-title">The scenario</div><p>${s.scenario}</p>
        <div class="panel-title">Run it live</div>
        <ol class="uc-steps">${(s.steps || []).map((x) => `<li>${x}</li>`).join("")}</ol>
        <div class="uc-ctas">${(s.ctas || []).map(renderScenarioCta).join("")}</div>
      </div>
      <div class="panel">
        <div class="panel-title">Why on this box</div>
        <ul class="uc-points">${(s.why || []).map((w) =>
          `<li><b>${esc(w.head)}</b> — ${esc(w.body)}</li>`).join("")}</ul>
        ${(s.playbooks || []).length ? `<div class="panel-title">NVIDIA playbooks on this box</div>
        <div class="uc-playbooks">${s.playbooks.map((p) =>
          `<button class="btn" data-runbook="${esc(p.slug)}">${esc(p.label)}</button>`).join("")}</div>` : ""}
      </div>
    </div>
    ${(s.notes || []).length ? `<details class="uc-notes"><summary>Presenter notes</summary>
    <ul>${s.notes.map((n) => `<li>${n}</li>`).join("")}</ul></details>` : ""}
  </section>`;
}

async function loadScenarios() {
  scenarioList = await apiJSON("/api/scenarios");
  // deregister old scenario pages, register current ones
  for (let i = PAGES.length - 1; i >= 0; i--) {
    if (PAGES[i].startsWith("usecase-") || PAGES[i].startsWith("custom-")) PAGES.splice(i, 1);
  }
  scenarioList.forEach((s) => PAGES.push(s.id));
  $("scenarioNav").innerHTML = scenarioList.map((s) =>
    `<a href="#${esc(s.id)}" data-page="${esc(s.id)}" class="nav-link">${esc(s.nav)}</a>` +
    (s.id === "usecase-safety"
      ? `<a href="#casework" data-page="casework" class="nav-link nav-sub">Case Works</a>` : "")
  ).join("");
  $("scenarioPages").innerHTML = scenarioList.map(renderScenarioPage).join("");
  route(); // deep links into scenario pages resolve once rendered
}

/* ---- Document output: scenario deliverables rendered as paper ---- */
let docoutData = null;
let docoutBusy = false;

function renderDocout(d) {
  const rows = (arr, f) => (arr || []).map(f).join("");
  return `<div class="cw-doc">
    <div class="cw-doc-watermark">DRAFT — FICTIONAL SAMPLE</div>
    <div class="cw-doc-head">
      <div class="cw-doc-agency">${esc(d.org)}<br><span>${esc(d.doc_type)} · FICTIONAL SAMPLE</span></div>
    </div>
    <h3 style="border:none;font-size:15px">${esc(d.title)}</h3>
    <div class="cw-doc-fields">${rows(d.meta, (m) =>
      `<div><label>${esc(m.label)}</label>${esc(m.value)}</div>`)}</div>
    ${rows(d.sections, (s) => `<h3>${esc(s.heading)}</h3><p class="cw-doc-narrative">${esc(s.body)}</p>`)}
    ${(d.checklist || []).length ? `<h3>Required follow-ups</h3>
      <ul>${rows(d.checklist, (c) => `<li>☐ ${esc(c)}</li>`)}</ul>` : ""}
    <p class="cw-doc-narrative" style="margin-top:16px"><i>${esc(d.signoff)}</i></p>
  </div>`;
}

async function docoutRun() {
  const prompt = $("docPrompt").value.trim();
  if (!prompt || docoutBusy) return;
  docoutBusy = true;
  $("docGo").disabled = true;
  $("docResultWrap").hidden = true;
  $("docProcessing").hidden = false;
  $("docProcStatus").textContent = "Drafting the document…";
  $("docProcMeta").textContent = "";
  try {
    await streamSSE("/api/docgen", { prompt }, {
      message: (d) => {
        if (d.type === "status") $("docProcStatus").textContent = d.status;
        else if (d.type === "progress") {
          $("docProcMeta").textContent = `${d.tokens} tokens · ${d.seconds}s · all on this device`;
        } else if (d.type === "result") {
          docoutData = d.data;
          $("docResult").innerHTML = renderDocout(d.data);
          $("docResultWrap").hidden = false;
          $("docProcessing").hidden = true;
          $("docResultMeta").textContent = "Drafted on this device — zero cloud";
        } else if (d.type === "error") {
          $("docProcStatus").textContent = `Error: ${d.error}`;
        }
      },
    });
  } catch (err) {
    $("docProcStatus").textContent = `Error: ${err.message}`;
  } finally {
    docoutBusy = false;
    $("docGo").disabled = false;
  }
}

$("docForm").addEventListener("submit", (e) => { e.preventDefault(); docoutRun(); });
$("docPrint").addEventListener("click", () => window.print());
$("docPdf").addEventListener("click", async () => {
  if (!docoutData) return;
  const r = await fetch("/api/docgen/pdf", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(docoutData),
  });
  window.open(URL.createObjectURL(await r.blob()), "_blank");
});

/* ---- Studio ---- */
let stDraft = null; // scenario being edited (draft or saved custom)

function stCtaRow(c, i) {
  return `<div class="st-cta" data-ctarow="${i}">
    <div class="st-cta-head"><label>Demo button ${i + 1}</label>
      <button type="button" class="linklike" data-ctadel="${i}">remove</button></div>
    <input type="text" data-ctalabel placeholder="Button label" value="${esc(c.label || "")}">
    <select data-ctatarget>
      ${["chat", "rag", "vision", "doc"].map((t) =>
        `<option value="${t}" ${c.target === t ? "selected" : ""}>${t}</option>`).join("")}
    </select>
    <textarea data-ctaprompt rows="2" placeholder="The prompt this button pre-fills">${esc(c.prompt || "")}</textarea>
  </div>`;
}

function stShowEditor(s) {
  stDraft = s;
  $("stEditor").hidden = false;
  $("stTitle").value = s.title || "";
  $("stNav").value = s.nav || "";
  $("stTagline").value = s.tagline || "";
  $("stScenario").value = s.scenario || "";
  $("stSteps").value = (s.steps || []).join("\n");
  $("stWhy").value = (s.why || []).map((w) => `${w.head} — ${w.body}`).join("\n");
  $("stNotes").value = (s.notes || []).join("\n");
  $("stCtas").innerHTML = (s.ctas || []).filter((c) => !c.href).map(stCtaRow).join("") +
    `<button type="button" class="btn" id="stCtaAdd">+ Add demo button</button>`;
  $("stGendoc").hidden = !s.id;  // doc generation once saved
  $("stSaveStatus").textContent = "";
}

function stCollect() {
  const lines = (id) => $(id.slice(1)).value.split("\n").map((x) => x.trim()).filter(Boolean);
  const ctas = [...document.querySelectorAll("#stCtas .st-cta")].map((row) => ({
    label: row.querySelector("[data-ctalabel]").value.trim(),
    target: row.querySelector("[data-ctatarget]").value,
    prompt: row.querySelector("[data-ctaprompt]").value.trim(),
    primary: false,
  })).filter((c) => c.label && c.prompt);
  if (ctas.length) ctas[0].primary = true;
  // keep non-editable href CTAs (e.g. the generated sample-PDF link)
  const hrefs = (stDraft?.ctas || []).filter((c) => c.href);
  return {
    ...(stDraft?.id ? { id: stDraft.id } : {}),
    title: $("stTitle").value.trim(),
    nav: $("stNav").value.trim(),
    tagline: $("stTagline").value.trim(),
    scenario: $("stScenario").value.trim(),
    steps: lines("#stSteps"),
    why: lines("#stWhy").map((l) => {
      const [head, ...rest] = l.split("—");
      return { head: head.trim(), body: rest.join("—").trim() };
    }),
    notes: lines("#stNotes"),
    ctas: [...ctas, ...hrefs],
    playbooks: stDraft?.playbooks || [],
  };
}

$("stGenerate").addEventListener("click", async () => {
  const industry = $("stIndustry").value.trim();
  const workflows = $("stWorkflows").value.trim();
  if (!industry || !workflows) {
    $("stGenStatus").textContent = "Industry and workflows are the minimum."; return;
  }
  $("stGenerate").disabled = true;
  $("stGenStatus").textContent = "Drafting…";
  try {
    await streamSSE("/api/scenarios/generate", {
      industry, workflows,
      sensitive: $("stSensitive").value.trim(), extra: $("stExtra").value.trim(),
    }, {
      message: (d) => {
        if (d.type === "progress") $("stGenStatus").textContent = `Drafting… ${d.tokens} tokens · ${d.seconds}s`;
        else if (d.type === "result") {
          $("stGenStatus").textContent = "Draft ready — edit and save.";
          stShowEditor({ ...d.data, custom: true });
        } else if (d.type === "error") $("stGenStatus").textContent = `Error: ${d.error}`;
      },
    });
  } catch (err) {
    $("stGenStatus").textContent = `Error: ${err.message}`;
  } finally {
    $("stGenerate").disabled = false;
  }
});

$("stCtas") && document.addEventListener("click", (e) => {
  if (e.target.id === "stCtaAdd") {
    e.target.insertAdjacentHTML("beforebegin",
      stCtaRow({ label: "", target: "chat", prompt: "" },
        document.querySelectorAll("#stCtas .st-cta").length));
  }
  const del = e.target.closest("[data-ctadel]");
  if (del) del.closest(".st-cta").remove();
  const ed = e.target.closest("[data-stedit]");
  if (ed) {
    const s = scenarioList.find((x) => x.id === ed.dataset.stedit);
    if (s) { location.hash = "studio"; stShowEditor(s); }
  }
  const dl = e.target.closest("[data-stdelete]");
  if (dl && confirm("Delete this custom scenario?")) {
    fetch(`/api/scenarios/${dl.dataset.stdelete}`, { method: "DELETE" })
      .then(() => { location.hash = "studio"; loadScenarios(); });
  }
});

$("stSave").addEventListener("click", async () => {
  try {
    const saved = await apiJSON("/api/scenarios", stCollect());
    stDraft = saved;
    $("stSaveStatus").textContent = "Saved — it's in the sidebar.";
    $("stGendoc").hidden = false;
    await loadScenarios();
  } catch (err) {
    $("stSaveStatus").textContent = `Error: ${err.message}`;
  }
});

$("stGendoc").addEventListener("click", async () => {
  if (!stDraft?.id) return;
  $("stGendoc").disabled = true;
  $("stSaveStatus").textContent = "Writing the sample document…";
  try {
    await streamSSE("/api/scenarios/gendoc", stDraft, {
      message: async (d) => {
        if (d.type === "progress") $("stSaveStatus").textContent = `Writing… ${d.tokens} tokens`;
        else if (d.type === "error") $("stSaveStatus").textContent = `Error: ${d.error}`;
        else if (d.type === "doc_done") {
          // wire the doc into the scenario: PDF link CTA + rag preselect
          stDraft.ctas = (stDraft.ctas || []).filter((c) => !c.href);
          stDraft.ctas.push({ label: "View the sample (PDF)", href: `/scenario-docs/${d.file}` });
          stDraft.ctas.forEach((c) => { if (c.target === "rag") c.ragdoc = stDraft.id; });
          await apiJSON("/api/scenarios", stDraft);
          await apiJSON("/api/rag/docs"); // nudge; ingestion happens server-side
          $("stSaveStatus").textContent = `Sample created: “${d.title}” (${d.sections} sections) — indexed for Docs Q&A.`;
          await loadScenarios();
          loadRagDocs();
        }
      },
    });
  } catch (err) {
    $("stSaveStatus").textContent = `Error: ${err.message}`;
  } finally {
    $("stGendoc").disabled = false;
  }
});

$("stImportFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const bundle = JSON.parse(await file.text());
    const saved = await apiJSON("/api/scenarios/import", bundle);
    $("stManageStatus").textContent = `Imported “${saved.title}”.`;
    await loadScenarios();
  } catch (err) {
    $("stManageStatus").textContent = `Import failed: ${err.message}`;
  }
  e.target.value = "";
});

/* ============================================================
   AI Agent (NemoClaw) status
   ============================================================ */
async function refreshAgentStatus() {
  let s;
  try {
    s = await apiJSON("/api/agent/status");
  } catch {
    return;
  }
  // the page only advertises itself when the agent actually exists —
  // customers never see an "isn't set up" screen (direct #agent still works)
  $("agentNavLink").hidden = !s.configured;
  $("agentOpen").href = s.ui_url;
  $("agentStatusLine").innerHTML = s.configured
    ? `<span class="pf-light ${s.ui_up ? "pf-ok" : "pf-bad"}" style="display:inline-block;margin-right:7px"></span>` +
      (s.ui_up
        ? `Sandbox <b>${s.sandbox}</b> running — ${s.agent} on ${s.model}, fully local`
        : `Sandbox <b>${s.sandbox}</b> configured but its UI isn't responding — run: nemoclaw ${s.sandbox} recover`)
    : "NemoClaw isn't set up on this box — open the playbook to install it.";
}
setInterval(refreshAgentStatus, 15000);

/* ============================================================
   Boot
   ============================================================ */
route();
connectTelemetry();
apiJSON("/api/config").then((c) => { CFG = c; }).catch(() => {})
  .finally(() => {
    loadModels().catch(() => { $("modelRow").innerHTML = `<span class="dim">⚠ Ollama not reachable — check the compute node's container</span>`; });
    loadBatchControls().catch(() => {});
    loadAgentWorks().catch(() => {});
    loadArena().catch(() => {});
    loadBuildoff().catch(() => {});
  });
loadOverview();
refreshMemoryView();
refreshModelDock();
refreshSavings();
loadRagDocs();
loadRunbooks();
loadScenarios().catch(() => {});
loadCasework().catch(() => {});
loadBench().catch(() => {});
loadLadder().catch((err) => {
  $("ldRun").disabled = true;
  $("ldNote").textContent = `Ladder unavailable: ${err.message}`;
});
refreshAgentStatus();
