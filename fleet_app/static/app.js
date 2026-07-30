const TOKEN_KEY = "fleet_token_seed"; // only seeds the input; auth uses session ticket
const TICKET_KEY = "fleet_session";
const POLL_MS = 3000;

const $ = (id) => document.getElementById(id);

function seedToken() {
  return $("token").value.trim() || localStorage.getItem(TOKEN_KEY) || "";
}

function ticket() {
  return sessionStorage.getItem(TICKET_KEY) || "";
}

function authHeaders() {
  const h = { "Content-Type": "application/json" };
  const t = ticket();
  if (t) h["X-Fleet-Session"] = t;
  else if (seedToken()) h["X-Fleet-Token"] = seedToken();
  return h;
}

async function ensureSession() {
  const tok = seedToken();
  if (!tok) throw Object.assign(new Error("401 — enter FLEET_TOKEN and press Save"), { status: 401 });
  const res = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: tok }),
  });
  if (!res.ok) {
    const err = new Error("401 — fleet token rejected. Paste FLEET_TOKEN from the control host and press Save.");
    err.status = 401;
    throw err;
  }
  const data = await res.json();
  sessionStorage.setItem(TICKET_KEY, data.ticket);
  localStorage.setItem(TOKEN_KEY, tok);
  return data.ticket;
}

async function api(path, opts = {}) {
  if (!ticket() && seedToken()) {
    try {
      await ensureSession();
    } catch (e) {
      throw e;
    }
  }
  const res = await fetch(path, {
    ...opts,
    headers: { ...authHeaders(), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    if (res.status === 401) {
      sessionStorage.removeItem(TICKET_KEY);
    }
    const err = new Error(`${res.status} — ${detail}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function fmtMb(v) {
  if (!v) return "0 MB";
  if (v >= 1024) return `${(v / 1024).toFixed(1)} GB`;
  return `${Math.round(v)} MB`;
}

function setLink(kind, text) {
  $("link").className = `link ${kind}`;
  $("linkText").textContent = text;
}

function setBanner(msg) {
  const el = $("banner");
  if (!msg) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.classList.remove("hidden");
  el.textContent = msg;
}

function reportError(err) {
  if (err && err.status === 401) {
    setLink("bad", "unauthorized");
    setBanner(
      "401 — fleet token rejected. On the control host check $env:FLEET_TOKEN, paste it above, press Save. Retrying every 3s.",
    );
  } else {
    setLink("bad", "control API unreachable");
    setBanner(
      "Control API is down. On the main PC run: pwsh scripts/run_control.ps1  (or python -m fleet_app.api). Retrying every 3s.",
    );
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function render(snap) {
  setBanner("");

  const chip = $("stateChip");
  chip.textContent = snap.pool_state;
  chip.className = `state-chip ${snap.pool_state}`;
  $("message").textContent = snap.message || "—";

  const runtime = snap.runtime || "stub";
  const anima = snap.anima || {};
  $("modeLine").textContent =
    runtime === "anima"
      ? "runtime=anima · probes on (single-node HF)"
      : "runtime=stub · memory pool demo";

  const animaBtn = $("btnAnima");
  if (runtime === "anima" && snap.pool_state === "ready" && anima.url) {
    animaBtn.href = anima.url + "/health";
    animaBtn.classList.remove("hidden");
  } else {
    animaBtn.classList.add("hidden");
  }

  const sel = $("preset");
  const meta = snap.preset_meta || (snap.presets || []).map((id) => ({ id, label: id }));
  if (sel.options.length !== meta.length) {
    sel.innerHTML = "";
    meta.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.label || p.id;
      sel.appendChild(opt);
    });
  }
  if (document.activeElement !== sel) sel.value = snap.preset;

  const busy = snap.pool_state === "starting" || snap.pool_state === "stopping";
  const desiredOn = snap.desired_state === "on";
  $("btnEnable").disabled = busy || desiredOn;
  $("btnDisable").disabled = busy || !desiredOn;
  $("btnEnable").textContent =
    snap.pool_state === "starting" ? "Enabling…" : "Enable pool";
  $("btnDisable").textContent =
    snap.pool_state === "stopping" ? "Disabling…" : "Disable pool";
  sel.disabled = desiredOn;

  const rss = snap.pool.workload_rss_mb || snap.pool.shard_rss_mb || 0;
  const target = snap.pool.target_stub_ram_mb || 0;
  const pct =
    runtime === "anima"
      ? Math.min(100, rss > 0 ? 35 + Math.min(65, rss / 100) : 0)
      : target > 0
        ? Math.min(100, (rss / target) * 100)
        : 0;
  $("poolFill").style.width = `${pct}%`;
  $("poolTitle").textContent = runtime === "anima" ? "Anima workload RSS" : "Stub pool RAM";
  if (runtime === "anima") {
    $("poolSummary").textContent =
      `Workload RSS ${fmtMb(rss)} · Anima :${anima.port || "?"} · included online ${snap.pool.online_included}/${snap.pool.included_nodes}` +
      (snap.plan_dirty ? " · plan modified" : "");
    $("poolHint").textContent =
      "Anima mode: probes attach to a local HF model on main. Stub pooling is a different shape — disable to switch.";
  } else {
    $("poolSummary").textContent =
      `Measured RSS ${fmtMb(rss)} · target stub ${fmtMb(target)} · included online ${snap.pool.online_included}/${snap.pool.included_nodes}` +
      (snap.plan_dirty ? " · plan modified (custom)" : "") +
      ` · theoretical budget ~${snap.pool.planning_usable_gb} GB across hosts`;
    $("poolHint").textContent =
      "Stub mode: bar = measured RSS / target stub. Planning GB is theoretical across hosts — not Anima’s heap.";
  }

  const nodes = snap.nodes || [];
  const anyOnline = nodes.some((n) => n.online);
  $("empty").classList.toggle("hidden", anyOnline);
  if (!anyOnline) {
    $("empty").textContent =
      "Control API is reachable but no agent has checked in. Run: python -m fleet_app.agent --node-id main";
  }

  const host = $("nodes");
  host.textContent = "";
  const locked = desiredOn;

  nodes.forEach((n) => {
    const card = el("article", "card");
    card.dataset.id = n.id;

    const head = el("div", "card-head");
    const left = el("div");
    left.appendChild(el("h3", null, n.label));
    left.appendChild(
      el("p", "role", `${n.role} · ${n.gross_ram_gb || "?"} GB sticker`),
    );
    const dot = el("div", `dot ${n.online ? "on" : "off"}`);
    dot.title = n.online ? "online" : "offline / no heartbeat";
    head.appendChild(left);
    head.appendChild(dot);
    card.appendChild(head);

    const stats = el("div", "stats");
    const rows = [
      ["Host used", `${fmtMb(n.ram_used_mb)} / ${fmtMb(n.ram_total_mb)}`],
      ["Available", fmtMb(n.ram_available_mb)],
      ["Workload RSS", fmtMb(n.shard_rss_mb)],
      [runtime === "anima" ? "Target stub" : "Target stub", fmtMb(n.target_stub_ram_mb || 0)],
      ["Included", n.included ? "yes" : "no"],
      ["Desired load", n.desired_shard_running ? "on" : "off"],
    ];
    rows.forEach(([k, v]) => {
      const cell = el("div");
      cell.appendChild(el("span", null, k));
      cell.appendChild(document.createTextNode(v));
      stats.appendChild(cell);
    });
    card.appendChild(stats);

    const usedPct = n.ram_total_mb ? Math.min(100, (n.ram_used_mb / n.ram_total_mb) * 100) : 0;
    const mini = el("div", "mini-bar");
    const fill = el("i");
    fill.style.width = `${usedPct}%`;
    mini.appendChild(fill);
    card.appendChild(mini);

    const actions = el("div", "card-actions");
    const btn = el(
      "button",
      "btn ghost toggle-node",
      n.included ? "Exclude from pool" : "Include in pool",
    );
    btn.type = "button";
    btn.dataset.id = n.id;
    btn.disabled = locked;
    btn.addEventListener("click", async () => {
      try {
        render(await api(`/api/nodes/${btn.dataset.id}/toggle`, { method: "POST", body: "{}" }));
      } catch (e) {
        setBanner(e.message);
      }
    });
    actions.appendChild(btn);
    card.appendChild(actions);

    host.appendChild(card);
  });
}

let ws = null;
let wsRetry = null;

function wsLive() {
  return ws && ws.readyState === WebSocket.OPEN;
}

function connectWs() {
  if (wsRetry) {
    clearTimeout(wsRetry);
    wsRetry = null;
  }
  if (ws) {
    ws.onclose = null;
    try {
      ws.close();
    } catch (_) {}
  }
  const cred = ticket() || seedToken();
  if (!cred) {
    setLink("bad", "no token");
    setBanner("Enter your FLEET_TOKEN above and press Save.");
    return;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // Sec-WebSocket-Protocol: fleet, <ticket-or-token>
  ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`, ["fleet", cred]);
  ws.onopen = () => setLink("ok", "live · 1 Hz");
  ws.onmessage = (ev) => {
    try {
      render(JSON.parse(ev.data));
      setLink("ok", "live · 1 Hz");
    } catch (_) {}
  };
  ws.onclose = (ev) => {
    if (ev.code === 4401 || ev.code === 1002) {
      setLink("bad", "unauthorized");
      setBanner("401 — telemetry rejected. Press Save with the correct FLEET_TOKEN.");
      return;
    }
    setLink("warn", `polling every ${POLL_MS / 1000}s (socket closed)`);
    wsRetry = setTimeout(connectWs, 5000);
  };
}

async function refreshOnce() {
  try {
    render(await api("/api/status"));
    if (!wsLive()) setLink("warn", `polling every ${POLL_MS / 1000}s`);
  } catch (e) {
    reportError(e);
  }
}

setInterval(() => {
  if (!wsLive()) refreshOnce();
}, POLL_MS);

$("saveToken").addEventListener("click", async () => {
  try {
    await ensureSession();
    connectWs();
    await refreshOnce();
  } catch (e) {
    reportError(e);
  }
});

$("token").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") $("saveToken").click();
});

$("btnEnable").addEventListener("click", async () => {
  try {
    $("btnEnable").disabled = true;
    $("btnEnable").textContent = "Enabling…";
    render(
      await api("/api/pool/enable", {
        method: "POST",
        body: JSON.stringify({ preset: $("preset").value }),
      }),
    );
  } catch (e) {
    setBanner(e.message);
  }
});

$("btnDisable").addEventListener("click", async () => {
  try {
    $("btnDisable").disabled = true;
    $("btnDisable").textContent = "Disabling…";
    render(await api("/api/pool/disable", { method: "POST", body: "{}" }));
  } catch (e) {
    setBanner(e.message);
  }
});

$("preset").addEventListener("change", async () => {
  try {
    render(await api(`/api/preset/${$("preset").value}`, { method: "POST", body: "{}" }));
  } catch (e) {
    setBanner(e.message);
  }
});

$("token").value = localStorage.getItem(TOKEN_KEY) || "dev-change-me";
(async () => {
  try {
    await ensureSession();
    connectWs();
    await refreshOnce();
  } catch (e) {
    reportError(e);
  }
})();
