/* ccFleet shared client — socket, identity, api helpers, toasts, roster. */
(function () {
  const CCFlet = {
    socket: null,
    user: { username: "operator", color: "#58a6ff" },
    handlers: {},   // event -> [fn]
  };
  window.CCFlet = CCFlet;

  // ---- identity ----
  try {
    const saved = localStorage.getItem("ccflet_user");
    if (saved) CCFlet.user.username = saved;
  } catch (e) {}

  // ---- theme (light / dark) ----
  // The <head> applies the saved choice before first paint (no flash); this just
  // keeps <html>.light in sync. Persisted per-browser; the `storage` listener below
  // syncs other tabs and the dashboard's node-detail iframes live on toggle.
  CCFlet.applyTheme = function (theme) {
    document.documentElement.classList.toggle("light", theme === "light");
  };
  CCFlet.toggleTheme = function () {
    const next = document.documentElement.classList.contains("light") ? "dark" : "light";
    CCFlet.applyTheme(next);
    try { localStorage.setItem("ccflet_theme", next); } catch (e) {}
  };

  // ---- api ----
  CCFlet.api = async function (url, method = "GET", body = null) {
    const opts = {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-CCFlet-User": JSON.stringify(CCFlet.user),
      },
    };
    if (body) opts.body = JSON.stringify({ ...body, user: CCFlet.user });
    try {
      const r = await fetch(url, opts);
      return await r.json();
    } catch (e) {
      CCFlet.toast("network error: " + e.message, "err");
      return { success: false, error: e.message };
    }
  };

  // ---- toasts ----
  CCFlet.toast = function (msg, kind = "") {
    const box = document.getElementById("toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = 0; setTimeout(() => el.remove(), 250); }, 3200);
  };

  // ---- operator commands (D8) ----
  CCFlet.loadCommands = async function () {
    try {
      const r = await fetch("/api/commands",
        { headers: { "X-CCFlet-User": JSON.stringify(CCFlet.user) } });
      return await r.json();
    } catch (e) { return { commands: [] }; }
  };

  // Render grouped command buttons into `container`. Remote (🛰) and local (🖥)
  // are visually distinct so it's always clear WHERE a command runs. Labels use
  // textContent (operator-authored, but keep the XSS discipline everywhere).
  CCFlet.renderCommandButtons = function (container, cmds, onRun) {
    container.innerHTML = "";
    if (!cmds || !cmds.length) {
      const e = document.createElement("div");
      e.className = "cmd-empty";
      e.textContent = "No commands here yet — add them on the Config page.";
      container.appendChild(e);
      return;
    }
    const groups = {};
    cmds.forEach((c) => (groups[c.group] = groups[c.group] || []).push(c));
    Object.keys(groups).forEach((g) => {
      const gd = document.createElement("div"); gd.className = "cmd-group";
      const lbl = document.createElement("div"); lbl.className = "lbl"; lbl.textContent = g;
      const row = document.createElement("div"); row.className = "cmd-row";
      groups[g].forEach((c) => {
        const b = document.createElement("button");
        b.className = "btn sm cmd " + (c.on === "local" ? "local" : "remote") +
                      (c.danger ? " danger" : "");
        const where = document.createElement("span");
        where.className = "where"; where.textContent = c.on === "local" ? "🖥" : "🛰";
        const t = document.createElement("span"); t.textContent = c.label;
        b.append(where, t);
        b.title = c.on === "local"
          ? "runs LOCALLY on the base station"
          : "runs on the node over SSH (role " + c.role + ")";
        b.onclick = () => onRun(c, b);
        row.appendChild(b);
      });
      gd.append(lbl, row);
      container.appendChild(gd);
    });
  };

  // ---- event bus over socket ----
  CCFlet.on = function (event, fn) {
    (CCFlet.handlers[event] = CCFlet.handlers[event] || []).push(fn);
    if (CCFlet.socket) CCFlet.socket.on(event, fn);
  };

  // The variant is stored as A/B internally and shown with a readable label. The
  // variant is per-node — each node's card/detail carries its own A/B toggle; there
  // is no global variant badge. Rename the labels here to suit your project.
  const VARIANT_LABEL = { A: "variant A", B: "variant B" };
  CCFlet.variantLabel = (v) => VARIANT_LABEL[v] || ("variant " + v);

  function renderRoster(users) {
    const box = document.getElementById("roster");
    if (!box) return;
    box.innerHTML = "";
    (users || []).forEach((u) => {
      const a = document.createElement("span");
      a.className = "avatar";
      a.style.background = u.color;
      a.textContent = (u.username || "?").slice(0, 2).toUpperCase();
      a.title = u.username;
      box.appendChild(a);
    });
  }

  // ---- States bar (status LEDs under the header) ----
  // One LED per configured state: a base-station ping link (green = reachable, red =
  // no reply) or a command-driven state (color set by the command's exit code via its
  // return_colors map). gray = not checked yet. All labels/details/hints are
  // operator-authored config → rendered with textContent (XSS).
  const STATE_COLORS = ["green", "yellow", "red", "blue", "purple", "orange", "gray"];
  function renderStateLeds(states) {
    const box = document.getElementById("stateLeds");
    if (!box) return;
    box.innerHTML = "";
    if (!states || !states.length) {
      const e = document.createElement("span");
      e.className = "muted states-empty";
      e.textContent = "no states configured";
      box.appendChild(e);
      return;
    }
    states.forEach((s) => {
      const color = STATE_COLORS.includes(s.color) ? s.color : "gray";
      const led = document.createElement("span");
      led.className = "led c-" + color + (s.kind ? " k-" + s.kind : "");
      const dot = document.createElement("span");
      dot.className = "led-dot";
      const lbl = document.createElement("span");
      lbl.className = "led-label";
      lbl.textContent = s.label;
      led.append(dot, lbl);
      led.title = s.label + (s.detail ? " — " + s.detail : "") +
        (s.hint ? " · " + s.hint : "");
      box.appendChild(led);
    });
  }
  CCFlet.renderStateLeds = renderStateLeds;

  // ---- pinned sessions (shared by the Sessions page + the bottom bar) ----
  // The set of session ids the operator pinned to the bottom bar, persisted in
  // localStorage so it survives navigation (the bar is global). The Sessions page
  // toggles pins; the bottom bar listens and re-renders its chips.
  const PIN_KEY = "ccflet_pinned";
  CCFlet.getPins = function () {
    try { const a = JSON.parse(localStorage.getItem(PIN_KEY) || "[]"); return Array.isArray(a) ? a : []; }
    catch (e) { return []; }
  };
  CCFlet.isPinned = function (sid) { return CCFlet.getPins().indexOf(sid) >= 0; };
  CCFlet._pinListeners = [];
  CCFlet.onPinsChanged = function (fn) { CCFlet._pinListeners.push(fn); };
  CCFlet.togglePin = function (sid) {
    const cur = CCFlet.getPins(), i = cur.indexOf(sid);
    if (i >= 0) cur.splice(i, 1); else cur.push(sid);
    try { localStorage.setItem(PIN_KEY, JSON.stringify(cur)); } catch (e) {}
    CCFlet._pinListeners.forEach((fn) => { try { fn(); } catch (e) {} });
    return cur.indexOf(sid) >= 0;
  };

  // ---- session dock (global bottom drawer: a reduced session view) ----
  // The bottom bar carries one chip per pinned session (plus the always-present
  // live session); clicking a chip opens that session in the dock. The live
  // session is interactive (comment / quick commands / close — these endpoints all
  // act on the server's CURRENT session); past sessions are read-only views.
  // Reuses the global `new_event` feed (every client joins FLEET_ROOM). No-ops on
  // embed pages (no dock element).
  function setupDock() {
    const dock = document.getElementById("sessionDock");
    const elTabs = document.getElementById("sessTabs");
    if (!dock || !elTabs) return;

    const elEvents = document.getElementById("dockEvents");
    const elName = document.getElementById("dockName");
    const elMeta = document.getElementById("dockMeta");
    const elStatus = document.getElementById("dockStatus");
    const elFull = document.getElementById("dockFull");
    const elCmds = document.getElementById("dockCmds");
    const elSide = dock.querySelector(".dock-side");
    const elRO = document.getElementById("dockRO");
    const elNoteInput = document.getElementById("dockNoteInput");
    const elNoteBtn = document.getElementById("dockNoteBtn");
    const elEnd = document.getElementById("dockEnd");
    const elCollapse = document.getElementById("dockCollapse");

    let SESS = [], CUR = null;       // session list + current id (server truth)
    let activeSid = null;            // which session the dock is showing
    let viewSid = null, canWrite = false, lastSeq = 0;
    const MAX_ROWS = 200;

    const byId = (id) => SESS.find((s) => s.session_id === id) || null;
    const fmtTime = (sid) => {
      const m = /^\d{4}-\d{2}-\d{2}_(\d{2})(\d{2})/.exec(sid || "");
      return m ? m[1] + ":" + m[2] : "";
    };
    const span = (cls, text) => {
      const e = document.createElement("span"); e.className = cls;
      if (text != null) e.textContent = text;   // textContent: notes/usernames are untrusted
      return e;
    };
    const kindClass = (t) => {
      t = t || "";
      if (t.includes("failed") || t.includes("error")) return "k-fail";
      if (t.includes("completed") || t === "daemon_started") return "k-ok";
      if (t.includes("sequence")) return "k-seq";
      if (t === "note") return "k-note";
      return "k-info";
    };
    const detail = (d) => {
      if (d.from || d.to) {                     // state_changed — a States-bar LED flipped
        return (d.label || d.key || "state") + " · " + (d.from || "?") + " → " + (d.to || "?");
      }
      let s = d.action || d.daemon || d.step || d.text || d.sequence ||
        (d.node ? "node " + d.node : "") || "";
      if (Array.isArray(d.results)) {           // custom-command completion
        const okc = d.results.filter((x) => x.success).length;
        s = (s ? s + " — " : "") + okc + "/" + d.results.length + " ok";
      }
      return s;
    };
    const addRow = (e) => {
      lastSeq = Math.max(lastSeq, e.seq || 0);
      const row = document.createElement("div");
      row.className = "ev " + kindClass(e.type);
      row.appendChild(span("t", (e.timestamp || "").slice(11, 19)));
      row.appendChild(span("ty", e.type));
      if (e.user) {
        const w = span("who", (e.user.username || "?").slice(0, 8));
        w.style.background = e.user.color || "#58a6ff";
        row.appendChild(w);
      }
      row.appendChild(span("d", detail(e.data || {})));
      elEvents.appendChild(row);
      while (elEvents.children.length > MAX_ROWS) elEvents.removeChild(elEvents.firstChild);
      elEvents.scrollTop = elEvents.scrollHeight;
    };

    // ---- bottom-bar chips: dedup([live, ...pinned]) that still exist ----
    const chipOrder = () => {
      const out = [], seen = {};
      const add = (id) => { if (id && byId(id) && !seen[id]) { seen[id] = 1; out.push(id); } };
      add(CUR);
      CCFlet.getPins().forEach(add);
      return out;
    };
    const renderChips = () => {
      elTabs.innerHTML = "";
      const order = chipOrder();
      if (!order.length) {
        elTabs.appendChild(span("muted sess-empty", "no sessions — pin them on the Sessions page"));
        return;
      }
      order.forEach((id) => {
        const s = byId(id), live = id === CUR, pinned = CCFlet.isPinned(id);
        const chip = document.createElement("button");
        chip.className = "sess-chip" + (id === activeSid ? " active" : "") + (live ? " live" : "");
        chip.dataset.sid = id;
        chip.appendChild(span("sc-dot"));
        chip.appendChild(span("sc-name", s.name || id));
        const t = fmtTime(id); if (t) chip.appendChild(span("sc-time", t));
        chip.title = id + " · " + s.status + (live ? " · live" : "");
        if (pinned && !live) {                 // live chip is always present → not removable
          const x = span("sc-x", "×"); x.title = "unpin from the bottom bar";
          x.onclick = (ev) => {
            ev.stopPropagation();
            CCFlet.togglePin(id);              // fires onPinsChanged → renderChips
            if (activeSid === id) selectSession(CUR);
          };
          chip.appendChild(x);
        }
        chip.onclick = () => chipClick(id);
        elTabs.appendChild(chip);
      });
    };

    const setHeader = (s) => {
      viewSid = s ? s.session_id : null;
      const live = !!(s && s.session_id === CUR);
      canWrite = live && !!(s && s.status === "open");   // write endpoints act on CURRENT only
      elName.textContent = s ? (s.name || s.session_id) : "no session";
      elStatus.className = "dock-status" + (canWrite ? " open" : "");
      elMeta.textContent = s
        ? ((live ? "live · " : "") + s.status + " · started " +
           (s.created_at || "").slice(0, 19).replace("T", " ") +
           (s.closed_at ? " · closed " + s.closed_at.slice(11, 19) : ""))
        : "— pin a session on the Sessions page";
      elFull.href = viewSid ? "/sessions/" + encodeURIComponent(viewSid) : "/sessions";
      elSide.classList.toggle("disabled", !canWrite);
      if (elRO) elRO.style.display = (s && !canWrite) ? "" : "none";
      [elNoteInput, elNoteBtn, elEnd].forEach((x) => { x.disabled = !canWrite; });
    };

    const loadEvents = async () => {
      elEvents.innerHTML = ""; lastSeq = 0;
      if (!viewSid) return;
      const r = await CCFlet.api("/api/sessions/" + encodeURIComponent(viewSid) + "/events?after=0");
      (r.events || []).slice(-MAX_ROWS).forEach(addRow);
    };

    // Point the dock at a session (updates chips highlight + header + log).
    const selectSession = async (id) => {
      activeSid = id || null;
      renderChips();
      setHeader(id ? byId(id) : null);
      await loadEvents();
    };

    const runCmd = async (c, btn) => {
      if (!canWrite) return;
      btn.disabled = true;
      const r = await CCFlet.api("/api/fleet/command", "POST", { command: c.name });
      btn.disabled = false;
      const res = r.results || [], okc = res.filter((x) => x.success).length;
      const where = c.on === "local" ? "base station" : "whole fleet";
      CCFlet.toast(`${c.label} → ${where}: ${okc}/${res.length} ok`, r.ok ? "ok" : "err");
    };
    const loadCmds = async () => {
      const data = await CCFlet.loadCommands();
      const list = (data.commands || []).filter((c) => c.scope === "fleet");
      CCFlet.renderCommandButtons(elCmds, list, runCmd);
    };

    const loadSessions = async () => {
      const d = await CCFlet.api("/api/sessions");
      CUR = (d && d.current) || null;
      SESS = (d && d.sessions) || [];
    };
    // Keep showing the active session if it's still on the bar, else fall back to live.
    const onBar = (id) => id && (id === CUR || CCFlet.isPinned(id));
    const refresh = async () => {
      await loadSessions();
      const target = onBar(activeSid) ? activeSid
        : (CUR || (SESS[0] && SESS[0].session_id) || null);
      await selectSession(target);
    };

    const shown = () => dock.classList.contains("open");
    const open = () => {
      dock.classList.add("open"); dock.setAttribute("aria-hidden", "false");
      elTabs.querySelectorAll(".sess-chip").forEach((c) => c.classList.remove("unread"));
      elEvents.scrollTop = elEvents.scrollHeight;
    };
    const close = () => { dock.classList.remove("open"); dock.setAttribute("aria-hidden", "true"); };
    const chipClick = async (id) => {
      if (shown() && activeSid === id) { close(); return; }   // click the active chip → toggle shut
      await selectSession(id);
      open();
    };

    elCollapse.onclick = close;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && shown()) close(); });

    elNoteBtn.onclick = async () => {
      const n = elNoteInput.value.trim();
      if (!n || !canWrite) return;
      const r = await CCFlet.api("/api/session/note", "POST", { note: n });
      if (r.success) elNoteInput.value = "";
    };
    elNoteInput.addEventListener("keydown", (e) => { if (e.key === "Enter") elNoteBtn.onclick(); });

    elEnd.onclick = async () => {
      if (!canWrite) return;
      elEnd.disabled = true;
      const r = await CCFlet.api("/api/sessions/close", "POST");
      CCFlet.toast(r.success ? "session closed" : "no active session", r.success ? "ok" : "warn");
      if (r.success) refresh(); else elEnd.disabled = false;
    };

    // pins edited on the Sessions page (same window) → re-render the bar chips
    CCFlet.onPinsChanged(renderChips);

    // live feed: events stream for the CURRENT session only
    CCFlet.on("new_event", (e) => {
      if (e.type === "session_started") { refresh(); return; }   // new live session appeared
      if (e.type === "session_renamed") { refresh(); return; }   // chip labels changed
      if (activeSid === CUR) addRow(e);                          // append only when viewing live
      if (e.type === "session_closed") refresh();
      if (!shown()) {                                            // nudge the live chip
        const c = elTabs.querySelector(".sess-chip.live");
        if (c) c.classList.add("unread");
      }
    });
    CCFlet.on("commands_changed", loadCmds);

    refresh();
    loadCmds();
  }
  CCFlet.setupDock = setupDock;

  // ---- connect ----
  function connect() {
    const s = io();
    CCFlet.socket = s;
    s.on("connect", () => {
      const c = document.getElementById("conn");
      if (c) { c.classList.add("online"); document.getElementById("connText").textContent = "live"; }
      // apply saved name
      const inp = document.getElementById("opName");
      if (inp && CCFlet.user.username) inp.value = CCFlet.user.username;
      s.emit("set_username", { username: CCFlet.user.username });
    });
    s.on("disconnect", () => {
      const c = document.getElementById("conn");
      if (c) { c.classList.remove("online"); document.getElementById("connText").textContent = "offline"; }
    });
    s.on("user_info", (d) => {
      if (!localStorage.getItem("ccflet_user")) {
        CCFlet.user.username = d.username;
        const inp = document.getElementById("opName");
        if (inp) inp.value = d.username;
      }
      CCFlet.user.color = d.color;
    });
    s.on("roster", (d) => renderRoster(d.users));
    s.on("states_status", (d) => renderStateLeds(d.states));
    s.on("action_progress", (d) => {
      if (d.state === "failed") CCFlet.toast(`${d.node}/${d.action} failed`, "err");
    });
    // re-bind any handlers registered before connect
    Object.entries(CCFlet.handlers).forEach(([ev, fns]) => fns.forEach((fn) => s.on(ev, fn)));
  }

  // seed the States bar (until a states_status push arrives)
  CCFlet.api("/api/states").then((d) => { if (d && d.states) renderStateLeds(d.states); });

  document.addEventListener("DOMContentLoaded", () => {
    // nav active state
    const path = location.pathname;
    document.querySelectorAll(".nav a").forEach((a) => {
      const k = a.dataset.nav;
      if ((k === "dashboard" && path === "/") ||
          (k === "sessions" && path.startsWith("/sessions")) ||
          (k === "config" && path.startsWith("/config")) ||
          (k === "help" && path.startsWith("/help"))) a.classList.add("active");
    });
    // theme toggle (header, top-right) + cross-tab/iframe sync
    const themeBtn = document.getElementById("themeToggle");
    if (themeBtn) themeBtn.addEventListener("click", CCFlet.toggleTheme);
    window.addEventListener("storage", (e) => {
      if (e.key === "ccflet_theme") CCFlet.applyTheme(e.newValue);
    });
    // operator name input
    const inp = document.getElementById("opName");
    if (inp) {
      inp.value = CCFlet.user.username;
      inp.addEventListener("change", () => {
        CCFlet.user.username = inp.value.trim() || "operator";
        try { localStorage.setItem("ccflet_user", CCFlet.user.username); } catch (e) {}
        if (CCFlet.socket) CCFlet.socket.emit("set_username", { username: CCFlet.user.username });
      });
    }
    connect();
    setupDock();
  });
})();
