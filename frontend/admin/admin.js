(() => {
  /* ── Constants ──────────────────────────────────────── */
  const TOKEN_KEY = "vs_admin_token";
  const TAB_KEY   = "vs_admin_tab";

  const TAB_META = {
    dashboard: {
      eyebrow:  "Overview",
      title:    "Clinic Dashboard",
      subtitle: "Live overview of assessments and appointments.",
    },
    reports: {
      eyebrow:  "Patients",
      title:    "Assessment Reports",
      subtitle: "Search and inspect patient AI smile report outcomes.",
    },
    appointments: {
      eyebrow:  "Patients",
      title:    "Appointments",
      subtitle: "Track booking status and manage upcoming visits.",
    },
    book: {
      eyebrow:  "Patients",
      title:    "New Booking",
      subtitle: "Create an in-person appointment for a walk-in patient.",
    },
    hours: {
      eyebrow:  "Settings",
      title:    "Clinic Hours",
      subtitle: "Manage seasonal opening days and time windows.",
    },
  };

  /* ── State ──────────────────────────────────────────── */
  let token         = safeGetToken();
  let pollTimer     = null;
  let reportsDebounce  = null;
  let bookingsDebounce = null;

  /* ── Helpers ────────────────────────────────────────── */
  const $  = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  function safeGetToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
  }
  function safeSetToken(val) {
    try {
      if (val) localStorage.setItem(TOKEN_KEY, val);
      else localStorage.removeItem(TOKEN_KEY);
    } catch { /* ignore */ }
  }
  function safeGetTab() {
    try { return localStorage.getItem(TAB_KEY) || ""; } catch { return ""; }
  }
  function safeSetTab(tab) {
    try { localStorage.setItem(TAB_KEY, tab); } catch { /* ignore */ }
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatWhen(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        hour12: true,
      });
    } catch { return iso; }
  }

  /** Format HH:MM or HH:MM:SS as 12-hour time with AM/PM. */
  function formatTime12(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const match = raw.match(/^(\d{1,2}):(\d{2})/);
    if (!match) return raw;
    let hours = Number(match[1]);
    const minutes = Number(match[2]);
    if (Number.isNaN(hours) || Number.isNaN(minutes) || hours > 23 || minutes > 59) return raw;
    const suffix = hours >= 12 ? "PM" : "AM";
    const hour12 = hours % 12 || 12;
    return `${hour12}:${String(minutes).padStart(2, "0")} ${suffix}`;
  }

  function formatBookingWhen(dateStr, timeStr) {
    const day = String(dateStr || "").trim();
    const time = formatTime12(timeStr);
    if (day && time) return `${day} · ${time}`;
    return day || time || "";
  }

  function toErrorText(ex) {
    if (!ex) return "Login failed. Please try again.";
    if (typeof ex === "string") return ex;
    if (ex instanceof Error && typeof ex.message === "string") return ex.message;
    if (typeof ex === "object") {
      const d = ex.detail;
      if (typeof d === "string") return d;
      if (Array.isArray(d) && d.length) {
        const first = d[0] || {};
        if (typeof first.msg === "string") return first.msg;
      }
      if (typeof ex.message === "string") return ex.message;
    }
    return "Login failed. Check your password and try again.";
  }

  /* Derive a score-range badge */
  function scoreBadge(score) {
    if (score == null) return `<span class="badge badge-grey">No score</span>`;
    const n = Number(score);
    if (n >= 80) return `<span class="badge badge-ok">⭐ ${n}</span>`;
    if (n >= 60) return `<span class="badge badge-navy">${n}</span>`;
    if (n >= 40) return `<span class="badge badge-warn">${n}</span>`;
    return `<span class="badge badge-danger">${n}</span>`;
  }

  /* Status badge for appointments */
  function statusBadge(status) {
    if (status === "confirmed") return `<span class="badge badge-ok">Confirmed</span>`;
    if (status === "cancelled") return `<span class="badge badge-danger">Cancelled</span>`;
    return `<span class="badge badge-grey">${escapeHtml(status)}</span>`;
  }

  /* Source badge */
  function sourceBadge(source) {
    if (source === "admin") return `<span class="badge badge-navy">Admin</span>`;
    return `<span class="badge badge-teal">${escapeHtml(source || "Patient")}</span>`;
  }

  /* ── API wrapper ────────────────────────────────────── */
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const res  = await fetch(path, { ...options, headers });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      token = "";
      safeSetToken("");
      setLoggedIn(false);
      throw new Error("Session expired. Please sign in again.");
    }
    if (!res.ok) {
      let detail = "Request failed";
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail) && data.detail.length) detail = String(data.detail[0]?.msg || detail);
      else if (typeof data.message === "string") detail = data.message;
      throw new Error(detail);
    }
    return data;
  }

  /* ── Auth state ─────────────────────────────────────── */
  function setLoggedIn(on) {
    $("#login-screen").hidden = !!on;
    $("#app-shell").hidden    = !on;
    if (on) { startPolling(); refreshAll(); }
    else     { stopPolling(); }
  }

  /* ── Polling ────────────────────────────────────────── */
  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }
  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
      const active = $(".tab.is-active")?.dataset.tab;
      if (active === "dashboard")    loadStats();
      if (active === "reports")      loadReports();
      if (active === "appointments") loadBookings();
      if (active === "hours")        loadSchedules();
    }, 30_000);
  }

  /* ── Tab switching ──────────────────────────────────── */
  function setTopMeta(tabName) {
    const meta = TAB_META[tabName] || TAB_META.dashboard;
    const eyebrowEl = $("#topbar-eyebrow");
    const titleEl   = $("#top-title");
    const subEl     = $("#top-subtitle");
    if (eyebrowEl) eyebrowEl.textContent = meta.eyebrow;
    if (titleEl)   titleEl.textContent   = meta.title;
    if (subEl)     subEl.textContent     = meta.subtitle;
  }

  function showTab(name) {
    $$(".tab").forEach((t) => {
      const isActive = t.dataset.tab === name;
      t.classList.toggle("is-active", isActive);
      t.setAttribute("aria-selected", String(isActive));
    });
    $$(".panel").forEach((p) => {
      const on = p.id === `panel-${name}`;
      p.hidden = !on;
      p.classList.toggle("is-active", on);
    });
    setTopMeta(name);
    safeSetTab(name);
    if (name === "dashboard")    loadStats();
    if (name === "reports")      loadReports();
    if (name === "appointments") loadBookings();
    if (name === "hours")        loadSchedules();
    if (name === "book")         refreshWalkinHint();
  }

  function refreshAll() {
    const active = $(".tab.is-active")?.dataset.tab || "dashboard";
    showTab(active);
  }

  /* ── Dashboard ──────────────────────────────────────── */
  async function loadStats() {
    try {
      const data = await api("/admin/api/stats");
      $("#stat-assessments").textContent = data.assessment_count ?? "—";
      $("#stat-bookings").textContent    = data.booking_count    ?? "—";
      $("#stat-avg").textContent =
        data.avg_smile_score != null ? data.avg_smile_score : "—";

      $("#top-concerns").innerHTML = (data.top_concerns || [])
        .map((r, i) =>
          `<li>
            <span class="rank-num">${i + 1}</span>
            <span class="rank-label">${escapeHtml(r.label)}</span>
            <span class="rank-count">${r.count}</span>
          </li>`
        ).join("") || emptyState("No data yet");

      $("#top-treatments").innerHTML = (data.top_treatments || [])
        .map((r, i) =>
          `<li>
            <span class="rank-num">${i + 1}</span>
            <span class="rank-label">${escapeHtml(r.label)}</span>
            <span class="rank-count">${r.count}</span>
          </li>`
        ).join("") || emptyState("No data yet");
    } catch (e) {
      console.warn("[admin] loadStats:", e);
    }
  }

  /* ── Reports ────────────────────────────────────────── */
  async function loadReports() {
    const q       = $("#reports-q").value.trim();
    const [sort, order] = ($("#reports-sort").value || "created_at:desc").split(":");
    const min     = $("#reports-min").value;
    const max     = $("#reports-max").value;
    const params  = new URLSearchParams({ sort, order, q });
    if (min !== "") params.set("min_score", min);
    if (max !== "") params.set("max_score", max);
    const list = $("#reports-list");
    try {
      const data = await api(`/admin/api/reports?${params}`);
      if (!data.items?.length) {
        list.innerHTML = emptyState("No reports found matching your search.");
        return;
      }
      list.innerHTML = data.items.map((r) => `
        <button type="button" class="data-card" data-report="${r.id}" role="listitem">
          <div class="data-card-inner">
            <div class="data-card-body">
              <div class="data-card-title">
                ${escapeHtml(r.email || "—")}
                ${scoreBadge(r.overall_score)}
              </div>
              <div class="meta">
                ${r.phone ? `<span class="meta-item">
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12.17 19.79 19.79 0 0 1 1.61 3.6 2 2 0 0 1 3.58 1.4h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 9a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
                  ${escapeHtml(r.phone)}
                </span>` : ""}
                <span class="meta-item">
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                  ${formatWhen(r.created_at)}
                </span>
                ${(r.concerns || []).slice(0, 2).map((c) =>
                  `<span class="badge badge-grey" style="font-weight:500">${escapeHtml(c)}</span>`
                ).join("")}
              </div>
            </div>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-top:4px" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
          </div>
        </button>
      `).join("");
    } catch (e) {
      list.innerHTML = errorState(e.message);
    }
  }

  /* Report detail drawer */
  async function openReport(id) {
    try {
      const data     = await api(`/admin/api/reports/${id}`);
      const r        = data.report;
      const bookings = data.bookings || [];
      const score    = r.overall_score != null ? Number(r.overall_score) : null;
      const scorePct = score != null ? `${score}%` : "70%";

      $("#drawer-title").textContent = "Assessment Detail";
      $("#drawer-body").innerHTML = `
        ${score != null ? `
          <div class="score-ring-wrap">
            <div class="score-ring" style="--score-pct:${scorePct}">
              <span>${score}</span>
            </div>
            <div>
              <div style="font-weight:700;color:var(--navy);font-size:1.05rem">Smile Score</div>
              <div style="font-size:0.82rem;color:var(--ink-soft);margin-top:0.2rem">
                ${score >= 80 ? "Excellent condition" : score >= 60 ? "Good condition" : score >= 40 ? "Some concerns" : "Needs attention"}
              </div>
            </div>
          </div>
        ` : ""}

        <div class="drawer-meta-grid" style="margin-bottom:1.25rem">
          <div class="drawer-meta-item">
            <div class="drawer-meta-label">Email</div>
            <div class="drawer-meta-value">${escapeHtml(r.email || "—")}</div>
          </div>
          <div class="drawer-meta-item">
            <div class="drawer-meta-label">Phone</div>
            <div class="drawer-meta-value">${escapeHtml(r.phone || "—")}</div>
          </div>
          <div class="drawer-meta-item">
            <div class="drawer-meta-label">Submitted</div>
            <div class="drawer-meta-value">${formatWhen(r.created_at) || "—"}</div>
          </div>
          <div class="drawer-meta-item">
            <div class="drawer-meta-label">Email status</div>
            <div class="drawer-meta-value">${r.email_sent_at
              ? `<span class="badge badge-ok">Sent</span>`
              : `<span class="badge badge-grey">Not sent</span>`}</div>
          </div>
        </div>

        ${(r.concerns || []).length ? `
          <div class="drawer-section">
            <div class="drawer-section-title">Identified concerns</div>
            <div class="drawer-tags">
              ${(r.concerns || []).map((c) => `<span class="badge badge-warn">${escapeHtml(c)}</span>`).join("")}
            </div>
          </div>
        ` : ""}

        ${(r.treatments || []).length ? `
          <div class="drawer-section">
            <div class="drawer-section-title">Suggested treatments</div>
            <div class="drawer-tags">
              ${(r.treatments || []).map((t) => `<span class="badge badge-teal">${escapeHtml(t)}</span>`).join("")}
            </div>
          </div>
        ` : ""}

        ${bookings.length ? `
          <div class="drawer-section">
            <div class="drawer-section-title">Linked appointments</div>
            ${bookings.map((b) => `
              <div class="data-card" style="margin-bottom:0.45rem">
                <div class="meta">
                  <span class="meta-item">${escapeHtml(formatBookingWhen(b.date, b.time))}</span>
                  ${statusBadge(b.status)}
                </div>
              </div>
            `).join("")}
          </div>
        ` : ""}

        ${r.report_text ? `
          <div class="drawer-section">
            <div class="drawer-section-title">AI report</div>
            <div class="drawer-section-value" style="background:var(--surface-soft);border:1px solid var(--line);border-radius:var(--r-sm);padding:0.85rem;font-size:0.83rem;line-height:1.65;">${escapeHtml(r.report_text)}</div>
          </div>
        ` : ""}
      `;
      $("#drawer").hidden = false;
    } catch (e) {
      alert(e.message);
    }
  }

  /* ── Bookings ───────────────────────────────────────── */
  async function loadBookings() {
    const q       = $("#bookings-q").value.trim();
    const [sort, order] = ($("#bookings-sort").value || "date:desc").split(":");
    const status  = $("#bookings-status").value;
    const params  = new URLSearchParams({ sort, order, q, status });
    const list    = $("#bookings-list");
    let lastError = null;

    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const data = await api(`/admin/api/bookings?${params}`);
        if (!data.items?.length) {
          list.innerHTML = emptyState("No appointments found.");
          return;
        }
        list.innerHTML = data.items.map((b) => `
        <div class="data-card" role="listitem">
          <div class="data-card-inner">
            <div class="data-card-body">
              <div class="data-card-title">
                ${escapeHtml(b.name)}
                ${statusBadge(b.status)}
                ${sourceBadge(b.source)}
              </div>
              <div class="meta">
                <span class="meta-item">
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                  ${escapeHtml(formatBookingWhen(b.date, b.time))}
                </span>
                <span class="meta-item">
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
                  ${escapeHtml(b.email)}
                </span>
                <span class="meta-item">
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12.17 19.79 19.79 0 0 1 1.61 3.6 2 2 0 0 1 3.58 1.4h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 9a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
                  ${escapeHtml(b.phone)}
                </span>
              </div>
              ${b.note ? `<p class="data-card-note">${escapeHtml(b.note)}</p>` : ""}
            </div>
          </div>
          ${b.status === "confirmed" ? `
            <div class="data-card-actions">
              <button type="button" class="btn-danger-sm" data-cancel="${b.id}">
                <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                Cancel appointment
              </button>
            </div>
          ` : ""}
        </div>
      `).join("");
        return;
      } catch (e) {
        lastError = e;
        if (attempt < 3) {
          await new Promise((r) => setTimeout(r, 250 * attempt));
        }
      }
    }
    list.innerHTML = errorState(lastError?.message || "Could not load appointments. Please try again.");
  }

  /* ── Schedules ──────────────────────────────────────── */
  async function loadSchedules() {
    const list = $("#schedules-list");
    try {
      const data = await api("/admin/api/schedules");
      if (!data.items?.length) {
        list.innerHTML = emptyState("No schedules yet. Add one below.");
        return;
      }
      list.innerHTML = data.items.map((s) => {
        const days = (s.days_of_week || [])
          .map((d) => ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][d] || d)
          .join(" · ");
        return `
          <div class="data-card" role="listitem">
            <div class="data-card-inner">
              <div class="data-card-body">
                <div class="data-card-title">
                  ${escapeHtml(s.label)}
                  ${s.active
                    ? `<span class="badge badge-ok">Active</span>`
                    : `<span class="badge badge-grey">Inactive</span>`}
                </div>
                <div class="meta">
                  <span class="meta-item">
                    <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                    ${escapeHtml(s.start_date)} → ${escapeHtml(s.end_date)}
                  </span>
                  <span class="meta-item">
                    <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                    ${formatTime12(s.open_time)} - ${formatTime12(s.close_time)} · ${s.slot_minutes}min slots
                  </span>
                  <span class="meta-item">${escapeHtml(days)}</span>
                </div>
              </div>
            </div>
            <div class="data-card-actions">
              <button type="button" class="btn-danger-sm" data-del-schedule="${s.id}">
                <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
                Delete
              </button>
            </div>
          </div>
        `;
      }).join("");
    } catch (e) {
      list.innerHTML = errorState(e.message);
    }
  }

  /* ── Walk-in available slots ────────────────────────── */
  async function refreshWalkinHint() {
    const day  = $("#w-date").value;
    const hint = $("#w-slots-hint");
    const time = $("#w-time");
    if (!hint || !time) return;
    if (!day) {
      hint.textContent = "";
      time.innerHTML = `<option value="">Select a date first</option>`;
      time.disabled = true;
      time.value = "";
      return;
    }
    try {
      const res  = await fetch(`/api/availability?date=${encodeURIComponent(day)}`);
      const data = await res.json();
      if (!res.ok) {
        hint.textContent = data.detail || "Could not load slots.";
        time.innerHTML = `<option value="">No open times</option>`;
        time.disabled = true;
        return;
      }
      const slots = Array.isArray(data.slots) ? data.slots : [];
      if (!slots.length) {
        hint.textContent = data.closed
          ? "Clinic closed on this date."
          : "No free slots on this date.";
        time.innerHTML = `<option value="">No open times</option>`;
        time.disabled = true;
        return;
      }
      const previous = time.value;
      const selected = slots.includes(previous) ? previous : slots[0];
      time.innerHTML =
        `<option value="">Select a time</option>` +
        slots
          .map((slot) => `<option value="${escapeHtml(slot)}" ${slot === selected ? "selected" : ""}>${escapeHtml(formatTime12(slot))}</option>`)
          .join("");
      time.disabled = false;
      time.value = selected;
      if (data.open_time && data.close_time) {
        hint.textContent = `Clinic hours: ${formatTime12(data.open_time)} - ${formatTime12(data.close_time)}`;
      } else {
        hint.textContent = `Clinic hours: ${formatTime12(slots[0])} - ${formatTime12(slots[slots.length - 1])}`;
      }
    } catch {
      hint.textContent = "Could not load slots.";
      time.innerHTML = `<option value="">Could not load times</option>`;
      time.disabled = true;
    }
  }

  /* ── Empty / error states ───────────────────────────── */
  function emptyState(msg) {
    return `<div class="empty-state">
      <div class="empty-state-icon">📭</div>
      <p>${escapeHtml(msg)}</p>
    </div>`;
  }

  function errorState(msg) {
    return `<div class="empty-state" style="color:var(--danger)">
      <div class="empty-state-icon">⚠️</div>
      <p>${escapeHtml(msg)}</p>
    </div>`;
  }

  /* ── Login ──────────────────────────────────────────── */
  async function handleLoginSubmit(e) {
    if (e) e.preventDefault();
    const errEl    = $("#login-error");
    const errText  = $("#login-error-text");
    errEl.hidden   = true;
    const submit   = $("#login-submit");
    if (submit) { submit.disabled = true; submit.textContent = "Signing in…"; }
    try {
      const data = await fetch("/admin/api/login", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ password: $("#admin-password").value }),
      }).then(async (r) => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || "Login failed");
        return j;
      });
      token = data.token;
      safeSetToken(token);
      const pw = $("#admin-password");
      if (pw) pw.value = "";
      setLoggedIn(true);
    } catch (ex) {
      if (errText) errText.textContent = toErrorText(ex);
      errEl.hidden = false;
    } finally {
      if (submit) {
        submit.disabled    = false;
        submit.innerHTML   = `
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
          Sign in`;
      }
    }
  }

  /* ── Event bindings ─────────────────────────────────── */
  $("#login-form").addEventListener("submit", handleLoginSubmit);

  $("#logout-btn").addEventListener("click", () => {
    token = "";
    safeSetToken("");
    setLoggedIn(false);
  });

  $$(".tab").forEach((t) =>
    t.addEventListener("click", () => showTab(t.dataset.tab))
  );

  // Reports filters
  ["reports-q","reports-sort","reports-min","reports-max"].forEach((id) => {
    $(`#${id}`)?.addEventListener("input", () => {
      clearTimeout(reportsDebounce);
      reportsDebounce = setTimeout(loadReports, 200);
    });
    $(`#${id}`)?.addEventListener("change", loadReports);
  });

  // Bookings filters
  ["bookings-q","bookings-sort","bookings-status"].forEach((id) => {
    $(`#${id}`)?.addEventListener("input", () => {
      clearTimeout(bookingsDebounce);
      bookingsDebounce = setTimeout(loadBookings, 200);
    });
    $(`#${id}`)?.addEventListener("change", loadBookings);
  });

  // Open report drawer
  $("#reports-list")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-report]");
    if (btn) openReport(btn.dataset.report);
  });

  // Cancel booking
  $("#bookings-list")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-cancel]");
    if (!btn) return;
    if (!confirm("Cancel this appointment? This cannot be undone.")) return;
    try {
      await api(`/admin/api/bookings/${btn.dataset.cancel}`, {
        method: "PATCH",
        body:   JSON.stringify({ status: "cancelled" }),
      });
      loadBookings();
      loadStats();
    } catch (ex) { alert(ex.message); }
  });

  // Delete schedule
  $("#schedules-list")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-del-schedule]");
    if (!btn) return;
    if (!confirm("Delete this schedule? This cannot be undone.")) return;
    try {
      await api(`/admin/api/schedules/${btn.dataset.delSchedule}`, { method: "DELETE" });
      loadSchedules();
    } catch (ex) { alert(ex.message); }
  });

  // Drawer close
  $$("[data-close-drawer]").forEach((el) =>
    el.addEventListener("click", () => { $("#drawer").hidden = true; })
  );

  // ESC closes drawer
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#drawer").hidden) $("#drawer").hidden = true;
  });

  // Walk-in date → slots hint
  $("#w-date")?.addEventListener("change", refreshWalkinHint);

  // Walk-in form submit
  $("#walkin-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("#walkin-status");
    const timeVal = $("#w-time")?.value || "";
    if (!$("#w-date")?.value || !timeVal) {
      status.textContent = "Choose a date and an available time from clinic hours.";
      status.className = "status is-error";
      return;
    }
    status.textContent = "Saving…";
    status.className   = "status";
    try {
      await api("/admin/api/bookings", {
        method: "POST",
        body:   JSON.stringify({
          name:   $("#w-name").value.trim(),
          email:  $("#w-email").value.trim(),
          phone:  $("#w-phone").value.trim(),
          date:   $("#w-date").value,
          time:   timeVal,
          note:   $("#w-note").value.trim() || null,
          source: "admin",
        }),
      });
      status.textContent = "Booking confirmed successfully.";
      status.className   = "status is-ok";
      e.target.reset();
      refreshWalkinHint();
      loadBookings();
      loadStats();
    } catch (ex) {
      status.textContent = ex.message;
      status.className   = "status is-error";
    }
  });

  // Schedule form submit
  $("#schedule-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("#schedule-status");
    status.textContent = "Saving…";
    status.className   = "status";
    const days = $$('input[name="s-dow"]:checked').map((el) => Number(el.value));
    try {
      await api("/admin/api/schedules", {
        method: "POST",
        body:   JSON.stringify({
          label:         $("#s-label").value.trim(),
          start_date:    $("#s-start").value,
          end_date:      $("#s-end").value,
          days_of_week:  days,
          open_time:     $("#s-open").value,
          close_time:    $("#s-close").value,
          slot_minutes:  Number($("#s-step").value) || 30,
          active:        $("#s-active").checked,
        }),
      });
      status.textContent = "✓ Schedule saved.";
      status.className   = "status is-ok";
      e.target.reset();
      $("#s-active").checked = true;
      $("#s-open").value     = "09:00";
      $("#s-close").value    = "20:00";
      $("#s-step").value     = "30";
      $$('input[name="s-dow"]').forEach((el) => {
        el.checked = ["1","2","3","4","5","6"].includes(el.value);
      });
      loadSchedules();
    } catch (ex) {
      status.textContent = ex.message;
      status.className   = "status is-error";
    }
  });

  // Refresh on window focus
  window.addEventListener("focus", () => { if (token) refreshAll(); });

  /* ── Bootstrap ──────────────────────────────────────── */
  if (token) {
    setLoggedIn(true);
    const preferredTab = safeGetTab();
    if (preferredTab) showTab(preferredTab);
  }
})();
