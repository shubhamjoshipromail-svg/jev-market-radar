// Radar dashboard: pulse chart + feed + detail. Data contract: PLAN.md (/api/feed?judged=1, /api/stats, /api/stream).
const $ = (s, r = document) => r.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const S = { feed: [], sel: null, hours: 6, filter: "alerts", minRel: 0.7, seen: new Set(), open: new Set(), votes: {} };
const DIR = { resolves_yes: 1, raises_yes: 1, resolves_no: -1, lowers_yes: -1, no_effect: 0 };
const LABEL = { resolves_yes: "Resolves YES", raises_yes: "YES more likely", resolves_no: "Resolves NO",
  lowers_yes: "YES less likely", no_effect: "No effect" };
const ORDER = ["resolves_yes", "raises_yes", "no_effect", "lowers_yes", "resolves_no"];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cents = (p) => (p == null ? "–" : `${(p * 100).toFixed(p < 0.1 || p > 0.9 ? 1 : 0)}¢`);
const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const ago = (iso) => { const s = (Date.now() - new Date(iso)) / 1e3;
  return s < 60 ? "now" : s < 3600 ? `${~~(s / 60)}m` : s < 86400 ? `${~~(s / 3600)}h` : `${~~(s / 86400)}d`; };
const src = (h) => (h.source || "").replace(/^(rss|bluesky):/, "").replace(/\.(com|org|social|bsky\.social)$/, "");
const svg = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };

// ---- plain-language layer: what an alert means for a user, not how Jev scored it -----------------
const VERDICT = {
  whatif: { t: "What-if", d: "You pasted this headline. This is what it would move if it were true; it isn't live news.", p: 1.5 },
  settled: { t: "Settled, price lagging", d: "The news basically decides this bet, and the price hasn't caught up.", p: 0 },
  early: { t: "Early", d: "Real news on a traded bet. The market hasn't reacted yet.", p: 1 },
  quiet: { t: "No reaction", d: "30+ minutes and the price hasn't moved. Traders didn't act on this.", p: 3 },
  priced: { t: "Already priced in", d: "The price already moved the way we expected. You're late on this one.", p: 2 },
  fyi: { t: "FYI", d: "Related, but not alert-worthy: thin market, weak news, or already near its limit.", p: 4 },
};
const EFFECT_SENTENCE = { resolves_yes: "Settles it as YES", raises_yes: "Makes YES more likely", resolves_no: "Settles it as NO",
  lowers_yes: "Makes YES less likely", no_effect: "No real effect" };
const plainStrength = (s) => (s >= 1.4 ? "Confirmed news" : s >= 0.7 ? "Reported, not final" : "Rumour or opinion");
function verdict(j) {
  if (j.alert && j._paste) return "whatif";
  if (!j.alert) return j.relevant >= S.minRel && DIR[j.effect] ? "fyi" : null;
  const at = j.alert.price_at_alert ?? j.yes_price_at, now = j.market.yes_price;
  const moved = now != null && at != null ? (now - at) * j.alert.direction : 0;
  if (moved >= 0.02) return "priced";
  const age = (Date.now() - new Date(j.alert.created_at || 0)) / 60e3;
  if (age > 30) return "quiet"; // "early" only means something while the news is fresh
  return j.alert.kind === "stale_price" ? "settled" : "early";
}
function yesName(j) { const o = j.market.outcomes; return o && o[0] && o[0] !== "Yes" ? o[0] : "YES"; }
function trackLine(kind) {
  const t = S.track?.[kind];
  if (!t || t.moved < 5) return "Track record: still collecting (fewer than 5 similar alerts with a price move).";
  return `Track record: similar alerts moved the predicted way ${t.right} of ${t.moved} times.`;
}
async function loadTrack() {
  try { const s = await api("/api/scorecard"); S.track = Object.fromEntries(s.by_kind.map((k) => [k.key, k])); } catch {}
}

async function api(path, opts) { const r = await fetch(path, opts); if (!r.ok) throw new Error(await r.text()); return r.json(); }

// ---- derived per-headline numbers --------------------------------------------------------------
function summarize(it) {
  const rel = it.judgments.filter((j) => j.relevant >= S.minRel);
  return { rel, up: rel.filter((j) => DIR[j.effect] > 0).length, down: rel.filter((j) => DIR[j.effect] < 0).length,
    alerts: it.judgments.filter((j) => j.alert).length, t: new Date(it.headline.fetched_at) };
}

// ---- data --------------------------------------------------------------------------------------
async function load() {
  try { S.feed = (await api("/api/feed?judged=true&limit=300")).filter(Boolean); } catch (e) { console.warn(e); }
  for (const it of S.feed) for (const j of it.judgments) j._paste = it.headline.source === "paste";
  if (S.sel == null && S.feed.length) S.sel = (S.feed.find((it) => it.judgments.some((j) => j.alert)) || S.feed[0]).headline.id;
  renderAll();
}
async function loadStats() {
  try {
    const s = await api("/api/stats");
    const k = [["Headlines", s.headlines], ["Judgments", s.judgments?.toLocaleString()], ["Alerts", s.alerts],
      ["Right direction · 10m", s.direction_right_10m == null ? "–" : `${Math.round(s.direction_right_10m * 100)}%`],
      ["Price moved at all", s.moved_share_10m == null ? "–" : `${Math.round(s.moved_share_10m * 100)}% of ${s.checked_10m}`],
      ["Jev spend", `$${(s.cost_usd || 0).toFixed(3)}`], ["p50 / call", s.p50_latency_ms ? `${Math.round(s.p50_latency_ms)}ms` : "–"]];
    $("#kpis").innerHTML = k.map(([a, b]) => `<div><dt>${a}</dt><dd>${b ?? "–"}</dd></div>`).join("");
  } catch {}
}

// ---- pulse: diverging barcode of headline impact over time ------------------------------------
function renderPulse() {
  const box = $("#pulse"), tip = $("#tip");
  box.querySelector("svg")?.remove();
  const W = box.clientWidth, H = box.clientHeight, m = { l: 34, r: 8, t: 10, b: 20 };
  const t1 = Date.now(), t0 = t1 - S.hours * 3600e3;
  const items = S.feed.map((it) => ({ it, ...summarize(it) })).filter((d) => d.t >= t0);
  const maxN = Math.max(3, ...items.map((d) => Math.max(d.up, d.down)));
  const x = (t) => m.l + ((t - t0) / (t1 - t0)) * (W - m.l - m.r);
  const mid = m.t + (H - m.t - m.b) / 2, half = (H - m.t - m.b) / 2 - 6;
  const y = (n) => (n / maxN) * half;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${items.length} judged headlines` });
  box.prepend(root);
  // grid + axis
  const g = svg("g", { class: "grid" }, root), ax = svg("g", { class: "axis" }, root);
  const step = S.hours <= 1 ? 10 * 60e3 : S.hours <= 6 ? 3600e3 : 4 * 3600e3;
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    svg("line", { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b }, g);
    svg("text", { x: x(t), y: H - 5, "text-anchor": "middle" }, ax).textContent = hhmm(new Date(t).toISOString());
  }
  for (const n of [maxN, -maxN]) svg("text", { x: m.l - 6, y: mid - Math.sign(n) * y(Math.abs(n)) + 3, "text-anchor": "end" }, ax).textContent = Math.abs(n);
  svg("line", { class: "mid", x1: m.l, x2: W - m.r, y1: mid, y2: mid }, root);
  const bw = Math.max(2, Math.min(6, (W - m.l - m.r) / (S.hours * 30)));
  const cross = svg("line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" }, root);
  for (const d of items) {
    const cx = x(+d.t), sel = d.it.headline.id === S.sel;
    const gg = svg("g", { class: "bar", opacity: S.sel && !sel ? 0.55 : 1 }, root);
    if (d.up) svg("rect", { x: cx - bw / 2, y: mid - y(d.up) - 1, width: bw, height: y(d.up), rx: Math.min(2, bw / 2), fill: css("--yes") }, gg);
    if (d.down) svg("rect", { x: cx - bw / 2, y: mid + 1, width: bw, height: y(d.down), rx: Math.min(2, bw / 2), fill: css("--no") }, gg);
    if (!d.up && !d.down) svg("rect", { x: cx - bw / 2, y: mid - 1, width: bw, height: 2, fill: css("--line-2") }, gg);
    if (d.alerts) svg("rect", { x: cx - 3.5, y: mid - y(d.up) - 13, width: 7, height: 7, fill: css("--warn"),
      transform: `rotate(45 ${cx} ${mid - y(d.up) - 9.5})` }, gg);
    if (sel) svg("rect", { x: cx - bw / 2 - 2, y: m.t, width: bw + 4, height: H - m.t - m.b, fill: "none", stroke: css("--ink"), rx: 3 }, gg);
    const hit = svg("rect", { class: "hit", x: cx - 6, y: m.t, width: 12, height: H - m.t - m.b }, root);
    hit.addEventListener("mouseenter", () => {
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
      tip.hidden = false;
      tip.innerHTML = `<b>${esc(d.it.headline.title)}</b><div class="row"><span>${hhmm(d.it.headline.fetched_at)}</span>
        <span>▲ ${d.up}</span><span>▼ ${d.down}</span>${d.alerts ? `<span>◆ ${d.alerts} alert${d.alerts > 1 ? "s" : ""}</span>` : ""}</div>`;
      const tx = Math.min(Math.max(cx - 160, 0), W - 320);
      tip.style.left = `${tx}px`; tip.style.top = `${m.t}px`;
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
    hit.addEventListener("click", () => select(d.it.headline.id));
  }
  if (!items.length) svg("text", { x: W / 2, y: mid - 8, "text-anchor": "middle", class: "axis", fill: css("--ink-3") }, root)
    .textContent = "No judged headlines in this window yet";
}

// ---- feed list ------------------------------------------------------------------------------------
function miniBar(up, down) {
  const w = 52, s = Math.max(up + down, 1), r = svg("svg", { width: w, height: 6, "aria-hidden": "true" });
  const uw = (up / s) * w, dw = (down / s) * w;
  if (!up && !down) svg("rect", { x: 0, y: 2, width: w, height: 2, rx: 1, fill: css("--line-2") }, r);
  if (up) svg("rect", { x: 0, y: 0, width: Math.max(uw - (down ? 1 : 0), 2), height: 6, rx: 2, fill: css("--yes") }, r);
  if (down) svg("rect", { x: uw + (up ? 1 : 0), y: 0, width: Math.max(dw - (up ? 1 : 0), 2), height: 6, rx: 2, fill: css("--no") }, r);
  return r.outerHTML;
}
function renderFeed() {
  const list = S.feed.map((it) => ({ it, ...summarize(it) })).filter((d) => S.filter === "all" || d.it.judgments.some((j) => ["settled", "early"].includes(verdict(j)) || (verdict(j) === "whatif" && Date.now() - new Date(d.it.headline.fetched_at) < 3600e3)));
  const ol = $("#feed");
  if (!list.length) { ol.innerHTML = `<li class="empty">${S.filter === "alerts" ? "Nothing needs a look right now. That\u2019s normal: alerts are rare on purpose. Switch to All headlines, or try an example above." : "Waiting for headlines…"}</li>`; return; }
  ol.innerHTML = list.map((d) => { const h = d.it.headline;
    return `<li data-id="${h.id}" class="${h.id === S.sel ? "sel" : ""} ${S.seen.size && !S.seen.has(h.id) ? "fresh" : ""}">
      <button type="button"><span class="t">${hhmm(h.fetched_at)}</span><span class="h">${esc(h.title)}</span>
      <span class="mini">${miniBar(d.up, d.down)}${(() => { const best = d.it.judgments.map(verdict).filter((v) => v && v !== "fyi").sort((x, y) => VERDICT[x].p - VERDICT[y].p)[0];
        return best ? `<span class="vlabel v-${best}">${VERDICT[best].t}</span>` : ""; })()}</span>
      <span class="s">${esc(src(h))} · ${d.rel.length} of ${d.it.judgments.length} markets relevant</span></button></li>`; }).join("");
  list.forEach((d) => S.seen.add(d.it.headline.id));
}

// ---- detail panel ----------------------------------------------------------------------------------
function dist(probs) {
  const col = { resolves_yes: css("--yes"), raises_yes: css("--yes-soft"), no_effect: css("--mid"),
    lowers_yes: css("--no-soft"), resolves_no: css("--no") };
  return `<div class="dist" role="img" aria-label="Effect probabilities">${ORDER.map((k) =>
    `<i style="flex:${Math.max(probs?.[k] || 0, 0.001)};background:${col[k]}" title="${LABEL[k]} ${Math.round((probs?.[k] || 0) * 100)}%"></i>`).join("")}</div>
    <div class="dist-l"><span>YES ◂</span><span>no effect</span><span>▸ NO</span></div>`;
}
function reaction(a) {
  // price at alert, then the tracker's checks at +1/+5/+10/+60 min, on a compressed time axis
  const pts = [{ m: 0, p: a.price_at_alert }, ...(a.checks || []).map((c) => ({ m: c.offset_min, p: c.price }))].filter((d) => d.p != null);
  const W = 250, H = 64, pad = { l: 4, r: 30, t: 8, b: 14 };
  const xm = (m) => pad.l + (Math.sqrt(m) / Math.sqrt(60)) * (W - pad.l - pad.r);
  const ps = pts.map((d) => d.p), lo = Math.min(...ps) - 0.02, hi = Math.max(...ps) + 0.02;
  const yp = (p) => pad.t + (1 - (p - lo) / (hi - lo || 1)) * (H - pad.t - pad.b);
  const color = a.direction > 0 ? css("--yes") : css("--no");
  const last = pts[pts.length - 1], moved = last && pts.length > 1 ? (last.p - a.price_at_alert) * a.direction : null;
  const path = pts.map((d, i) => `${i ? "L" : "M"}${xm(d.m).toFixed(1)},${yp(d.p).toFixed(1)}`).join("");
  return `<svg class="react" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Price after alert">
    <line class="base" x1="${pad.l}" x2="${W - pad.r}" y1="${yp(a.price_at_alert)}" y2="${yp(a.price_at_alert)}"/>
    ${[0, 1, 5, 10, 60].map((mm) => `<text x="${xm(mm)}" y="${H - 2}" text-anchor="middle">${mm ? `+${mm}` : "0"}</text>`).join("")}
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${pts.map((d) => `<circle cx="${xm(d.m)}" cy="${yp(d.p)}" r="3.5" fill="${color}" stroke="${css("--surface")}" stroke-width="2"/>`).join("")}
    ${last ? `<text x="${W - pad.r + 4}" y="${yp(last.p) + 3}" text-anchor="start">${cents(last.p)}</text>` : ""}
  </svg>${pts.length < 2 ? `<div class="verdict">waiting for the +1 min check…</div>`
    : `<div class="verdict ${moved > 0 ? "hit" : "miss"}">${moved > 0 ? "✓ moved as predicted" : moved === 0 ? "– no move yet" : "✕ moved against"} (${moved > 0 ? "+" : ""}${(moved * 100).toFixed(1)}¢)</div>`}`;
}
function renderDetail() {
  const box = $("#detail"), it = S.feed.find((x) => x.headline.id === S.sel);
  if (!it) { box.innerHTML = `<p class="none">Select a headline, or paste one above.</p>`; return; }
  const h = it.headline, d = summarize(it);
  const js = [...it.judgments].sort((a, b) => (VERDICT[verdict(a)]?.p ?? 9) - (VERDICT[verdict(b)]?.p ?? 9) || b.relevant - a.relevant);
  const alerts = js.filter((j) => j.alert), rest = js.filter((j) => !j.alert && j.relevant >= S.minRel);
  const shown = rest;
  const look = alerts.filter((j) => ["settled", "early"].includes(verdict(j))).length;
  const summary = h.source === "paste" && alerts.length
    ? `If this were true, it would move <b>${alerts.length}</b> traded bet${alerts.length > 1 ? "s" : ""}. Nothing is pushed to phones for pasted headlines.`
    : alerts.length
    ? `This affects <b>${alerts.length}</b> traded bet${alerts.length > 1 ? "s" : ""}${look ? `, <b>${look}</b> worth a look now` : ", none still worth acting on"}.`
    : d.rel.length ? `Related to <b>${d.rel.length}</b> bet${d.rel.length > 1 ? "s" : ""}, but nothing alert-worthy: thin markets, weak news, or already priced in.`
      : "No live bet is really affected by this headline.";
  const lat = it.judgments.map((j) => j.latency_ms).sort((a, b) => a - b);
  S.firstAlert = null;
  box.innerHTML = `<div class="d-head">
      <div class="meta"><span>${esc(src(h))}${h.also_reported_by?.length ? ` · also ${h.also_reported_by.map(src).map(esc).join(", ")}` : ""}</span><span>${hhmm(h.fetched_at)} · ${ago(h.fetched_at)} ago</span>
      ${h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noopener">source ↗</a>` : ""}</div>
      <h1>${esc(h.title)}</h1>
      <p class="summary">${summary}</p>
      <div class="d-kpis"><span><b>${it.judgments.length}</b>markets judged</span><span><b>${d.rel.length}</b>relevant</span>
      <span><b>${d.up}</b>toward YES</span><span><b>${d.down}</b>toward NO</span><span><b>${d.alerts}</b>alerts</span>
      <span><b>${lat.length ? lat[lat.length >> 1] : "–"}ms</b>median call</span></div></div>
    ${alerts.length ? `<ol class="mk cards">${alerts.map(card).join("")}</ol>` : ""}
    ${shown.length ? `<h2 class="sub-h">Also related (FYI)</h2><ol class="mk">${shown.map((j) => row(j)).join("")}</ol>`
      : alerts.length ? "" : `<p class="none">None of the ${it.judgments.length} candidate markets cleared relevance ${S.minRel.toFixed(2)}.</p>`}`;
}
function row(j) {
  const dir = DIR[j.effect] || 0, a = j.alert, open = S.open.has(j.id) || (j.id === S.firstAlert && !S.open.has(-j.id));
  const str = Math.round(j.strength ?? 0), moved = j.market.yes_price != null && j.yes_price_at != null ? j.market.yes_price - j.yes_price_at : 0;
  return `<li data-j="${j.id}" class="${open ? "open" : ""}">
    <button class="mk-row" type="button">
      <span class="dir ${dir > 0 ? "up" : dir < 0 ? "down" : "flat"}" aria-label="${LABEL[j.effect]}">${dir > 0 ? "▲" : dir < 0 ? "▼" : "·"}</span>
      <span class="q">${esc(j.market.question)}<span class="fx">
        ${a ? `<span class="al">${a.kind === "stale_price" ? "Stale price" : "Mover"}</span>` : ""}
        ${a?.why ? `<span class="why">${esc(a.why)}</span>` : ""}
        <span>${LABEL[j.effect]}${j.market.outcomes && j.market.outcomes[0] !== "Yes" ? ` (YES = ${esc(j.market.outcomes[0])})` : ""}</span>
        <span>conf ${(j.effect_conf ?? 0).toFixed(2)}</span>
        ${j.same_period != null && j.same_period < 0.5 ? `<span class="warn" title="Jev thinks the news is about a different date/meeting">timing mismatch</span>` : ""}
        ${j.market.is_game ? `<span class="warn" title="Single-game market: shown, never alerted">game market</span>` : ""}</span></span>
      <span class="meter"><span>relevance ${j.relevant.toFixed(2)}</span><span class="bar"><i style="width:${j.relevant * 100}%"></i></span>
        <span class="dots" title="Strength: speculation → developing → settled fact">${[0, 1, 2].map((i) => `<i class="${i <= str ? "on" : ""}"></i>`).join("")}</span></span>
      <span class="px"><b>${cents(j.market.yes_price)}</b><small>${Math.abs(moved) >= 0.005 ? `${moved > 0 ? "+" : ""}${(moved * 100).toFixed(1)}¢ since` : "YES now"}</small></span>
    </button>
    <div class="mk-more">
      <div><div class="side"><h3>Resolution rules</h3></div><div class="rules">${esc(j.market.rules)}</div>
        <p class="s" style="margin:8px 0 0"><a href="${esc(j.market.url)}" target="_blank" rel="noopener">Open on Polymarket ↗</a></p></div>
      <div class="side"><h3>How Jev split its answer</h3>${dist(j.effect_probs)}
        ${a ? `<h3>Price since alert</h3>${reaction(a)}
        <div class="vote" data-alert="${a.id}"><button data-v="1" class="${S.votes[a.id] === 1 ? "on" : ""}">Useful</button>
        <button data-v="-1" class="${S.votes[a.id] === -1 ? "on" : ""}">Wrong</button></div>` : ""}</div>
    </div></li>`;
}

function card(j) {
  const v = verdict(j), V = VERDICT[v], a = j.alert, yes = yesName(j);
  const at = a.price_at_alert ?? j.yes_price_at, now = j.market.yes_price;
  const dir = DIR[j.effect] || 0, open = S.open.has(j.id);
  const eff = EFFECT_SENTENCE[j.effect].replace("YES", yes);
  return `<li data-j="${j.id}" class="card-li ${open ? "open" : ""}"><article class="acard v-${v}">
    <header><span class="vlabel v-${v}">${V.t}</span><span class="vdesc">${V.d}</span></header>
    <h3><a href="${esc(j.market.url)}" target="_blank" rel="noopener">${esc(j.market.question)}</a></h3>
    <dl class="facts">
      <div><dt>Effect</dt><dd class="${dir > 0 ? "up" : "down"}">${dir > 0 ? "▲" : "▼"} ${esc(eff)}</dd></div>
      <div><dt>Price of ${esc(yes)}</dt><dd class="mono">${cents(at)} at the headline → ${cents(now)} now</dd></div>
      <div><dt>How solid</dt><dd>${plainStrength(j.strength)}</dd></div>
    </dl>
    ${a.why ? `<p class="whyline">${esc(a.why)}</p>` : ""}
    <p class="track">${trackLine(a.kind)}</p>
    <div class="acts">
      <a class="btn primary" href="${esc(j.market.url)}" target="_blank" rel="noopener">Open on Polymarket ↗</a>
      <button class="btn share" type="button" data-share="${a.id}">Copy share link</button>
      <span class="vote" data-alert="${a.id}"><button data-v="1" class="${S.votes[a.id] === 1 ? "on" : ""}">Useful</button><button data-v="-1" class="${S.votes[a.id] === -1 ? "on" : ""}">Wrong</button></span>
      <button class="btn ghost more" type="button">${open ? "Hide details" : "Details"}</button>
    </div>
  </article>
  <div class="mk-more">
    <div><div class="side"><h3>Resolution rules</h3></div><div class="rules">${esc(j.market.rules)}</div></div>
    <div class="side"><h3>How Jev split its answer</h3>${dist(j.effect_probs)}<h3>Price since alert</h3>${reaction(a)}
      <p class="s">relevance ${j.relevant.toFixed(2)} · confidence ${(j.effect_conf ?? 0).toFixed(2)} · strength ${(j.strength ?? 0).toFixed(1)}/2 · ${j.latency_ms}ms</p></div>
  </div></li>`;
}

function renderAll() { renderPulse(); renderFeed(); renderDetail(); }
function select(id) { S.sel = id; renderAll(); if (innerWidth < 900) $("#detail").scrollIntoView({ behavior: "smooth" }); }

// ---- events --------------------------------------------------------------------------------------
$("#feed").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) select(+li.dataset.id); });
$("#detail").addEventListener("click", async (e) => {
  const v = e.target.closest(".vote button");
  if (v) { const id = +v.parentElement.dataset.alert, vote = +v.dataset.v; S.votes[id] = vote; renderDetail();
    try { await api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ alert_id: id, vote }) }); } catch {}
    return; }
  const sh = e.target.closest("[data-share]");
  if (sh) { const url = `${location.origin}/a/${sh.dataset.share}`;
    try { await navigator.clipboard.writeText(url); sh.textContent = "Link copied"; } catch { prompt("Share link", url); }
    setTimeout(() => (sh.textContent = "Copy share link"), 1800); return; }
  const more = e.target.closest(".more");
  if (more) { const li = more.closest("li"), id = +li.dataset.j, isOpen = li.classList.toggle("open");
    more.textContent = isOpen ? "Hide details" : "Details"; S.open.delete(id); S.open.add(isOpen ? id : -id); return; }
  const r = e.target.closest(".mk-row"); if (!r) return;
  const li = r.parentElement, id = +li.dataset.j, isOpen = li.classList.toggle("open");
  S.open.delete(id); S.open.delete(-id); S.open.add(isOpen ? id : -id);
});
$("#range").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return;
  S.hours = +b.dataset.h; [...$("#range").children].forEach((x) => x.classList.toggle("on", x === b)); renderPulse(); });
$("#filter").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return;
  S.filter = b.dataset.f; [...$("#filter").children].forEach((x) => x.classList.toggle("on", x === b)); renderFeed(); });
$("#minRel").addEventListener("input", (e) => { S.minRel = +e.target.value; $("#minRelV").value = S.minRel.toFixed(2); renderAll(); });
$("#cmd").addEventListener("submit", async (e) => {
  e.preventDefault(); const title = $("#cmdInput").value.trim(); if (!title) return;
  const b = $("#cmdBtn"); b.disabled = true; b.textContent = "Judging…"; const t0 = performance.now();
  try { const it = await api("/api/headline", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    $("#cmdInput").value = ""; S.sel = it.headline.id; await load(); loadStats();
    b.textContent = `${((performance.now() - t0) / 1000).toFixed(2)}s`;
  } catch (err) { b.textContent = "Failed"; console.error(err); }
  finally { b.disabled = false; setTimeout(() => (b.textContent = "Judge"), 2200); }
});
document.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => {
  $("#cmdInput").value = b.dataset.example; $("#cmd").requestSubmit();
}));
addEventListener("keydown", (e) => { if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("#cmdInput").focus(); } });
addEventListener("resize", () => renderPulse());
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);

let t;
const es = new EventSource("/api/stream");
es.onopen = () => $("#live").classList.add("on");
es.onerror = () => $("#live").classList.remove("on");
["headline", "judgment", "alert"].forEach((ev) => es.addEventListener(ev, () => { clearTimeout(t); t = setTimeout(() => { load(); loadStats(); }, 800); }));
load(); loadStats(); loadTrack().then(renderDetail);
setInterval(loadStats, 15000);
setInterval(loadTrack, 60000);
setInterval(() => { load(); }, 60000);
