const $ = (id) => document.getElementById(id);

function money(n, digits = 0) {
  const abs = Math.abs(n);
  const opts = { style: "currency", currency: "USD", maximumFractionDigits: digits, minimumFractionDigits: digits };
  if (abs >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  return new Intl.NumberFormat("en-US", opts).format(n);
}

function num(n) {
  n = Math.round(n || 0);
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString("en-US");
}

function full(n) {
  return Math.round(n || 0).toLocaleString("en-US");
}

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function drawRank(canvas, history) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width = canvas.clientWidth * 2;
  const h = canvas.height = 280;
  ctx.clearRect(0, 0, w, h);
  if (!history.length) {
    ctx.fillStyle = "#5b6472";
    ctx.font = "22px DM Sans";
    ctx.fillText("No 30-day rank history", 24, h / 2);
    return;
  }
  const ranks = history.map((x) => x.rank);
  const min = Math.min(...ranks);
  const max = Math.max(...ranks);
  const span = Math.max(max - min, 1);
  ctx.beginPath();
  ranks.forEach((r, i) => {
    const x = 40 + (i * (w - 80)) / Math.max(ranks.length - 1, 1);
    const y = 36 + ((r - min) / span) * (h - 80);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = "#0f766e";
  ctx.lineWidth = 5;
  ctx.stroke();
  ctx.fillStyle = "#5b6472";
  ctx.font = "20px DM Sans";
  ctx.fillText("Better rank is lower", 40, h - 16);
  ctx.fillText("#" + min + " best", w - 180, 32);
}

function renderReport(d) {
  const e = d.estimate;
  const r = d.rank;
  const g = d.geo || {};
  const rd = d.rdap || {};
  const change = r.change30d || 0;
  const changeTxt = change === 0 ? "flat 30d" : (change > 0 ? "+" + change + " better" : Math.abs(change) + " worse");
  const badgeClass = r.ranked ? "wc-pill" : "wc-pill warn";
  $("report").hidden = false;
  $("report-inner").innerHTML = `
    <div class="wc-head">
      <img src="${esc(d.favicon)}" alt="" />
      <div>
        <h2>${esc(d.domain)}</h2>
        <p>${esc((d.meta && d.meta.title) || "Website traffic report")} · checked ${esc(d.checkedAt)}</p>
        <span class="${badgeClass}">${esc(r.source)} · #${full(r.value)}</span>
      </div>
    </div>
    <div class="wc-kpis">
      <article class="wc-kpi"><span>Estimated worth</span><strong>${money(e.worth)}</strong><b>6x yearly ad revenue</b></article>
      <article class="wc-kpi"><span>Daily visitors</span><strong>${num(e.dailyVisitors)}</strong><b>${full(e.dailyVisitors)} unique / day</b></article>
      <article class="wc-kpi"><span>Daily pageviews</span><strong>${num(e.dailyPageviews)}</strong><b>${e.pagesPerVisit.toFixed(2)} pages / visit</b></article>
      <article class="wc-kpi"><span>Daily ad revenue</span><strong>${money(e.dailyRevenue)}</strong><b>CPM ${money(e.cpm, 2)}</b></article>
    </div>
    <div class="wc-periods">
      ${period("Daily estimations", e.dailyVisitors, e.dailyPageviews, e.dailyRevenue, d.range.low.dailyRevenue, d.range.high.dailyRevenue)}
      ${period("Monthly estimations", e.monthlyVisitors, e.monthlyPageviews, e.monthlyRevenue, d.range.low.monthlyRevenue, d.range.high.monthlyRevenue)}
      ${period("Yearly estimations", e.yearlyVisitors, e.yearlyPageviews, e.yearlyRevenue, d.range.low.yearlyRevenue, d.range.high.yearlyRevenue)}
    </div>
    <div class="wc-grid">
      <div class="wc-card">
        <h3>Global rank trend</h3>
        <p class="wc-note">Current #${full(r.value)} · ${esc(changeTxt)}</p>
        <canvas id="rank-chart"></canvas>
      </div>
      <div class="wc-card">
        <h3>Site facts</h3>
        ${row("Server country", [g.city, g.country].filter(Boolean).join(", ") || "Unknown")}
        ${row("IP", g.ip || (d.dns[0] && d.dns[0].ip) || "—")}
        ${row("ISP / org", g.org || g.isp || "—")}
        ${row("Registrar", rd.registrar || "—")}
        ${row("Created", rd.created ? rd.created + (d.ageYears != null ? " · " + d.ageYears + " yrs" : "") : "—")}
        ${row("Expires", rd.expires || "—")}
        ${row("HTTPS", d.meta && d.meta.https ? "Yes" : "No / unknown")}
        ${row("Reachable", d.meta && d.meta.reachable ? "Yes (" + d.meta.status + ")" : "No")}
      </div>
    </div>
    <div class="wc-card" style="margin-top:12px">
      <h3>What this estimate means</h3>
      <p class="wc-note">${esc(d.method.traffic)}</p>
      <p class="wc-note">${esc(d.method.revenue)}</p>
      <p class="wc-note">${esc(d.method.worth)}</p>
      ${d.meta && d.meta.description ? "<p class='wc-note'>" + esc(d.meta.description) + "</p>" : ""}
    </div>
  `;
  drawRank($("rank-chart"), r.history || []);
}

function period(title, vis, pv, rev, lo, hi) {
  return `<article class="wc-box">
    <h3>${title}</h3>
    <div class="wc-money">${money(rev)}</div>
    <ul>
      <li><span>Unique visitors</span><strong>${full(vis)}</strong></li>
      <li><span>Pageviews</span><strong>${full(pv)}</strong></li>
      <li><span>Ad revenue range</span><strong>${money(lo)} – ${money(hi)}</strong></li>
    </ul>
  </article>`;
}

function row(k, v) {
  return `<div class="wc-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`;
}

async function analyze(domain) {
  const status = $("status");
  const btn = $("search-btn");
  status.hidden = false;
  status.className = "wc-status";
  status.textContent = "Fetching live rank, WHOIS and server location for " + domain + "...";
  btn.disabled = true;
  try {
    const res = await fetch("/api/analyze?domain=" + encodeURIComponent(domain));
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Lookup failed");
    status.textContent = "Live data loaded · " + data.rank.source;
    renderReport(data);
    $("report").scrollIntoView({ behavior: "smooth", block: "start" });
    history.replaceState(null, "", "?domain=" + encodeURIComponent(data.domain));
  } catch (err) {
    status.className = "wc-status err";
    status.textContent = err.message || "Could not analyze this domain.";
    $("report").hidden = true;
  } finally {
    btn.disabled = false;
  }
}

if ($("search-form")) {
  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    analyze($("domain-input").value);
  });

  document.querySelectorAll("[data-demo]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("domain-input").value = btn.dataset.demo;
      analyze(btn.dataset.demo);
    });
  });

  async function loadTops() {
    if (!$("tops-body")) return;
    try {
      const res = await fetch("/api/topsites");
      const rows = await res.json();
      $("tops-body").innerHTML = rows.map((r, i) => `<tr data-domain="${esc(r.domain)}">
        <td>${i + 1}</td>
        <td><div class="wc-dom"><img src="${esc(r.favicon)}" width="16" height="16" alt="" /> ${esc(r.domain)}</div></td>
        <td>#${full(r.rank)}</td>
        <td>${num(r.dailyVisitors)}</td>
        <td>${money(r.monthlyRevenue)}</td>
        <td>${money(r.worth)}</td>
      </tr>`).join("");
      $("tops-body").querySelectorAll("tr").forEach((tr) => {
        tr.addEventListener("click", () => {
          $("domain-input").value = tr.dataset.domain;
          analyze(tr.dataset.domain);
        });
      });
    } catch {
      $("tops-body").innerHTML = "<tr><td colspan='6'>Could not load top sites right now.</td></tr>";
    }
  }

  loadTops();
  const q = new URLSearchParams(location.search).get("domain");
  if (q) {
    $("domain-input").value = q;
    analyze(q);
  }
}
