// Market Radar dashboard. Talks to the API contract in PLAN.md; falls back to mock_feed.json with no server.
const $ = (s) => document.querySelector(s);
const state = { feed: [], seen: new Set(), open: new Set(), votes: {} };
const EFFECT = {
  resolves_yes: ["▲▲", "up", "resolves YES"], raises_yes: ["▲", "up", "YES more likely"],
  resolves_no: ["▼▼", "down", "resolves NO"], lowers_yes: ["▼", "down", "YES less likely"],
  no_effect: ["·", "flat", "no effect"],
};
const pct = (p) => (p == null ? "–" : `${Math.round(p * 100)}¢`);
const ago = (iso) => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : iso.slice(0, 10);
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

async function loadFeed() {
  try { state.feed = await api("/api/feed?limit=100"); }
  catch { state.feed = await api("mock_feed.json"); }
  render();
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    const rows = [
      ["Headlines", s.headlines], ["Judgments", s.judgments], ["Alerts", s.alerts],
      ["Jev cost", s.cost_usd != null ? `$${Number(s.cost_usd).toFixed(4)}` : "–"],
      ["p50 latency", s.p50_latency_ms != null ? `${Math.round(s.p50_latency_ms)}ms` : "–"],
      ["10-min hit rate", s.hit_rate_10m != null ? `${Math.round(s.hit_rate_10m * 100)}%` : "–"],
    ];
    $("#stats").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v ?? "–"}</dd></div>`).join("");
  } catch { $("#stats").innerHTML = `<div><dt>Mode</dt><dd>mock data</dd></div>`; }
}

function judgmentRow(j) {
  const [arrow, cls, label] = EFFECT[j.effect] || EFFECT.no_effect;
  const a = j.alert;
  const voted = a && state.votes[a.id];
  return `<li class="j">
    <span class="arrow ${cls}" title="${label}">${arrow}</span>
    <div class="q"><a href="${esc(j.market.url)}" target="_blank" rel="noopener">${esc(j.market.question)}</a>
      <div class="chips">
        ${a ? `<span class="chip alert">${a.kind === "stale_price" ? "STALE PRICE" : "MOVER"}</span>` : ""}
        <span class="chip">${label}</span>
        <span class="chip">rel ${j.relevant?.toFixed(2)}</span>
        <span class="chip">conf ${j.effect_conf?.toFixed(2) ?? "–"}</span>
        <span class="chip">strength ${j.strength?.toFixed(1)}/2</span>
        <span class="chip">${j.latency_ms}ms</span>
      </div></div>
    <div class="price">${pct(j.market.yes_price)}<small>YES now</small>
      ${a ? `<div class="vote" data-alert="${a.id}">
        <button data-v="1" class="${voted === 1 ? "done" : ""}" title="Useful">👍</button>
        <button data-v="-1" class="${voted === -1 ? "done" : ""}" title="Wrong">👎</button></div>` : ""}
    </div></li>`;
}

function render() {
  const onlyJudged = $("#onlyJudged").checked, onlyAlerts = $("#onlyAlerts").checked;
  const minRel = +$("#minRel").value;
  $("#minRelV").textContent = minRel.toFixed(2);
  const items = state.feed.filter((it) => it && (!onlyJudged || it.judgments.length) &&
    (!onlyAlerts || it.judgments.some((j) => j.alert)));
  const feed = $("#feed");
  if (!items.length) { feed.innerHTML = `<p class="empty">Nothing yet. Paste a headline above, or wait for the news feed.</p>`; return; }
  feed.replaceChildren(...items.map((it) => {
    const h = it.headline, card = $("#tpl-card").content.firstElementChild.cloneNode(true);
    const shown = it.judgments.filter((j) => j.relevant >= minRel || j.alert);
    const alerts = it.judgments.filter((j) => j.alert).length;
    card.querySelector(".src").textContent = (h.source || "").replace(/^(rss|bluesky):/, "");
    card.querySelector(".title").textContent = h.title;
    card.querySelector(".meta").textContent = [ago(h.published_at || h.fetched_at),
      it.judgments.length ? `${it.judgments.length} markets judged` : h.status,
      shown.length ? `${shown.length} relevant` : "", alerts ? `${alerts} alert${alerts > 1 ? "s" : ""}` : ""]
      .filter(Boolean).join(" · ");
    card.querySelector(".judgments").innerHTML = shown.length ? shown.map(judgmentRow).join("")
      : `<li class="j"><span></span><span class="meta">No market above relevance ${minRel.toFixed(2)}</span></li>`;
    if (!state.open.has(h.id) && !(alerts || state.open.size === 0 && it === items[0])) card.classList.add("collapsed");
    if (!state.seen.has(h.id)) { if (state.seen.size) card.classList.add("fresh"); state.seen.add(h.id); }
    card.querySelector(".head").onclick = () => {
      card.classList.toggle("collapsed");
      card.classList.contains("collapsed") ? state.open.delete(h.id) : state.open.add(h.id);
    };
    return card;
  }));
}

$("#feed").addEventListener("click", async (e) => {
  const b = e.target.closest(".vote button"); if (!b) return;
  const id = +b.parentElement.dataset.alert, vote = +b.dataset.v;
  state.votes[id] = vote; render();
  try { await api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ alert_id: id, vote }) }); }
  catch (err) { console.warn(err); }
});

$("#paste").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button"), title = $("#title").value.trim();
  if (!title) return;
  btn.disabled = true; btn.textContent = "Judging…";
  const t0 = performance.now();
  try {
    const item = await api("/api/headline", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    state.open.add(item.headline.id);
    $("#title").value = "";
    await loadFeed(); loadStats();
    btn.textContent = `Done in ${((performance.now() - t0) / 1000).toFixed(1)}s`;
  } catch (err) { alert(`Failed: ${err.message}`); btn.textContent = "Judge it"; }
  finally { btn.disabled = false; setTimeout(() => (btn.textContent = "Judge it"), 2500); }
});

["#onlyJudged", "#onlyAlerts", "#minRel"].forEach((s) => $(s).addEventListener("input", render));

let pending;
function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => $("#live").classList.add("on");
  es.onerror = () => $("#live").classList.remove("on");
  const refresh = () => { clearTimeout(pending); pending = setTimeout(() => { loadFeed(); loadStats(); }, 600); };
  ["headline", "judgment", "alert"].forEach((ev) => es.addEventListener(ev, refresh));
}

loadFeed(); loadStats(); connect();
setInterval(loadStats, 15000);
setInterval(render, 60000); // keep "x min ago" fresh
