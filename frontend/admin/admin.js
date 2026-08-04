(() => {
  /* ── Constants ──────────────────────────────────────── */
  const TOKEN_KEY = "vs_admin_token";
  const TAB_KEY   = "vs_admin_tab";

  const PAKISTANI_CITIES = [
    "Abbottabad", "Attock", "Bahawalnagar", "Bahawalpur", "Burewala", "Chakwal",
    "Chaman", "Chiniot", "Chishtian", "Dadu", "Daska", "Dera Ghazi Khan",
    "Dera Ismail Khan", "Faisalabad", "Ferozewala", "Gilgit", "Gojra", "Gujranwala",
    "Gujrat", "Gwadar", "Hafizabad", "Haripur", "Hub", "Hyderabad", "Islamabad",
    "Jacobabad", "Jaranwala", "Jhelum", "Jhang", "Karachi", "Karianwala", "Kasur",
    "Khairpur", "Khanewal", "Khanpur", "Khuzdar", "Kohat", "Kot Abdul Malik",
    "Kotri", "Lahore", "Larkana", "Mandi Bahauddin", "Mardan", "Mirpur (AJK)",
    "Mirpur Khas", "Mingora", "Multan", "Muridke", "Muzaffarabad", "Muzaffargarh",
    "Nawabshah", "Okara", "Pakpattan", "Peshawar", "Quetta", "Rahim Yar Khan",
    "Rawalpindi", "Sadiqabad", "Sahiwal", "Samundri", "Sargodha", "Shahdadkot",
    "Sheikhupura", "Shikarpur", "Sialkot", "Skardu", "Sukkur", "Tando Adam",
    "Tando Allahyar", "Turbat", "Vehari", "Wah Cantt", "Other"
  ];

  const TAB_META = {
    dashboard: {
      eyebrow:  "Overview",
      title:    "Dashboard",
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
  let openReportId  = null;
  let openReportSeq = 0;
  let photoLoadSeq  = 0;
  const detailCache = new Map(); // id -> { data, fingerprint, at }
  const detailInflight = new Map(); // id -> Promise
  const listCache = new Map(); // queryKey -> { items, fingerprint, at }
  const thumbCache = new Map(); // assessmentId -> { objectUrl, sourceUrl, at }
  const thumbInflight = new Map(); // assessmentId -> Promise<string>
  const DETAIL_CACHE_STORAGE = "vs_admin_detail_cache_v1";
  const DETAIL_CACHE_MAX = 24;
  const THUMB_CACHE_MAX = 80;

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

  function formatIsoDate(dateStr) {
    const day = String(dateStr || "").trim();
    if (!day) return "";
    try {
      const parts = day.split("-").map(Number);
      if (parts.length >= 3 && parts.every((n) => Number.isFinite(n))) {
        const dt = new Date(parts[0], parts[1] - 1, parts[2]);
        return dt.toLocaleDateString(undefined, {
          day: "numeric",
          month: "short",
          year: "numeric",
        });
      }
    } catch { /* keep raw */ }
    return day;
  }

  function formatBookingWhen(dateStr, timeStr) {
    const day = String(dateStr || "").trim();
    if (!day) {
      const t = formatTime12(timeStr);
      return t ? t.replace(/\b(AM|PM)\b/g, (m) => m.toLowerCase()) : "—";
    }
    try {
      const parts = day.split("-").map(Number);
      if (parts.length >= 3 && parts.every((n) => Number.isFinite(n))) {
        const timeParts = String(timeStr || "00:00").split(":").map(Number);
        const dt = new Date(
          parts[0],
          parts[1] - 1,
          parts[2],
          Number.isFinite(timeParts[0]) ? timeParts[0] : 0,
          Number.isFinite(timeParts[1]) ? timeParts[1] : 0
        );
        return dt
          .toLocaleString(undefined, {
            day: "numeric",
            month: "short",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit",
            hour12: true,
          })
          .replace(/\b(AM|PM)\b/g, (m) => m.toLowerCase());
      }
    } catch { /* fall through */ }
    const datePart = formatIsoDate(day);
    const timePart = formatTime12(timeStr)?.replace(/\b(AM|PM)\b/g, (m) => m.toLowerCase()) || "";
    if (datePart && timePart) return `${datePart}, ${timePart}`;
    return datePart || timePart || "—";
  }

  function prettyLabel(raw) {
    return String(raw || "")
      .replace(/_/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  let dashState = {
    today: [],
    upcoming: [],
    selectedId: null,
    filter: "today",
    calendarDays: [],
    calendarMonth: "",
    notifications: [],
  };

  const NOTIF_SEEN_KEY = "vs_admin_notif_seen_v1";

  function readSeenNotifs() {
    try {
      const raw = sessionStorage.getItem(NOTIF_SEEN_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(parsed) ? parsed : []);
    } catch {
      return new Set();
    }
  }

  function writeSeenNotifs(ids) {
    try {
      sessionStorage.setItem(NOTIF_SEEN_KEY, JSON.stringify([...ids]));
    } catch { /* ignore */ }
  }

  function buildNotifications({ today = [], upcoming = [], assessmentCount = 0 } = {}) {
    const items = [];
    today.forEach((b) => {
      items.push({
        id: `today-${b.id}`,
        kind: "visit",
        title: b.name || "Patient visit",
        body: `Today at ${formatDashTime(b.time)}`,
        action: "appointments",
      });
    });
    upcoming
      .filter((b) => !today.some((t) => t.id === b.id))
      .slice(0, 4)
      .forEach((b) => {
        items.push({
          id: `up-${b.id}`,
          kind: "visit",
          title: b.name || "Upcoming visit",
          body: formatBookingWhen(b.date, b.time),
          action: "appointments",
        });
      });
    if (assessmentCount > 0) {
      items.push({
        id: `tests-${assessmentCount}`,
        kind: "test",
        title: "Assessment reports",
        body: `${assessmentCount} smile test${assessmentCount === 1 ? "" : "s"} on file`,
        action: "reports",
      });
    }
    return items.slice(0, 8);
  }

  function renderNotifications() {
    const list = $("#topbar-notify-list");
    const dot = $("#topbar-bell-dot");
    if (!list) return;
    const seen = readSeenNotifs();
    const items = dashState.notifications || [];
    const unread = items.filter((n) => !seen.has(n.id));
    if (dot) dot.hidden = unread.length === 0;

    if (!items.length) {
      list.innerHTML = `<p class="topbar-notify-empty">You're all caught up.</p>`;
      return;
    }

    list.innerHTML = items
      .map((n) => {
        const isUnread = !seen.has(n.id);
        const iconCls = n.kind === "test" ? " is-test" : "";
        return `
          <button type="button" class="topbar-notify-item${isUnread ? " is-unread" : ""}" data-notif-id="${escapeHtml(n.id)}" data-notif-action="${escapeHtml(n.action || "appointments")}">
            <span class="topbar-notify-icon${iconCls}" aria-hidden="true">
              ${n.kind === "test"
                ? `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`
                : `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`}
            </span>
            <span class="topbar-notify-copy">
              <strong>${escapeHtml(n.title)}</strong>
              <span>${escapeHtml(n.body)}</span>
            </span>
          </button>`;
      })
      .join("");
  }

  function closeNotifyPanel() {
    const wrap = $("#topbar-notify-wrap");
    const panel = $("#topbar-notify-panel");
    const btn = $("#topbar-bell");
    wrap?.classList.remove("is-open");
    if (panel) panel.hidden = true;
    btn?.setAttribute("aria-expanded", "false");
  }

  function openNotifyPanel() {
    const wrap = $("#topbar-notify-wrap");
    const panel = $("#topbar-notify-panel");
    const btn = $("#topbar-bell");
    wrap?.classList.add("is-open");
    if (panel) panel.hidden = false;
    btn?.setAttribute("aria-expanded", "true");
    renderNotifications();
  }

  function markNotifRead(id) {
    const seen = readSeenNotifs();
    seen.add(id);
    writeSeenNotifs(seen);
    renderNotifications();
  }

  function markAllNotifsRead() {
    const seen = readSeenNotifs();
    (dashState.notifications || []).forEach((n) => seen.add(n.id));
    writeSeenNotifs(seen);
    renderNotifications();
  }

  function closeDashFilterMenu() {
    const wrap = $("#dash-filter-wrap");
    const menu = $("#dash-filter-menu");
    const btn = $("#dash-filter-btn");
    wrap?.classList.remove("is-open");
    if (menu) menu.hidden = true;
    btn?.setAttribute("aria-expanded", "false");
  }

  function setDashFilter(value) {
    const next = value === "upcoming" ? "upcoming" : "today";
    dashState.filter = next;
    const label = $("#dash-filter-label");
    if (label) label.textContent = next === "upcoming" ? "Upcoming" : "Today";
    $$("#dash-filter-menu .clinic-filter-option").forEach((opt) => {
      const on = opt.dataset.value === next;
      opt.classList.toggle("is-active", on);
      opt.setAttribute("aria-selected", String(on));
    });
    closeDashFilterMenu();
    renderDashPatientList();
  }

  function renderScoreMix(el, rows) {
    if (!el) return;
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) {
      el.innerHTML = `<div class="dash-bars-empty">No scores yet</div>`;
      return;
    }
    const total = list.reduce((sum, r) => sum + (Number(r.count) || 0), 0) || 1;
    const segs = list
      .map((r) => {
        const count = Number(r.count) || 0;
        const key = String(r.key || "muted");
        if (!count) return "";
        return `<span class="dash-mix-seg is-${escapeHtml(key)}" style="flex:${Math.max(count, 0.01)}" title="${escapeHtml(r.hint || r.label)}: ${count}"></span>`;
      })
      .join("");
    const rowsHtml = list
      .map((r) => {
        const count = Number(r.count) || 0;
        const share = Math.round((count / total) * 100);
        const key = String(r.key || "muted");
        const name = String(r.hint || r.label || "").split(" ")[0] || "Band";
        return `
          <div class="dash-mix-row is-${escapeHtml(key)}">
            <div class="dash-mix-row-top">
              <span class="dash-mix-row-label">
                <span class="dash-mix-dot is-${escapeHtml(key)}" aria-hidden="true"></span>
                ${escapeHtml(name)}
              </span>
              <span class="dash-mix-row-meta">
                <strong>${count}</strong>
                <span>${share}%</span>
              </span>
            </div>
            <div class="dash-mix-track" aria-hidden="true">
              <span class="dash-mix-fill is-${escapeHtml(key)}" style="width:${share}%"></span>
            </div>
          </div>`;
      })
      .join("");
    el.innerHTML = `
      <div class="dash-mix-bar" role="img" aria-label="Score mix">${segs || `<span class="dash-mix-seg is-muted" style="flex:1"></span>`}</div>
      <div class="dash-mix-rows">${rowsHtml}</div>
    `;
  }

  function formatDashTime(timeStr) {
    return formatTime12(timeStr) || "—";
  }

  function visitTypeLabel(b) {
    if (b?.source === "admin") return "Clinic booking";
    if (b?.assessment_id) return "Assessment visit";
    return "In-person visit";
  }

  function visitTypeTone(b) {
    if (b?.source === "admin") return "navy";
    if (b?.assessment_id) return "teal";
    return "warn";
  }

  function currentPatientList() {
    return dashState.filter === "upcoming" ? dashState.upcoming : dashState.today;
  }

  function bookingDateTime(b) {
    const day = String(b?.date || "").trim();
    if (!day) return null;
    const parts = day.split("-").map(Number);
    if (parts.length < 3 || parts.some((n) => !Number.isFinite(n))) return null;
    const timeParts = String(b?.time || "00:00").split(":").map(Number);
    return new Date(
      parts[0],
      parts[1] - 1,
      parts[2],
      Number.isFinite(timeParts[0]) ? timeParts[0] : 0,
      Number.isFinite(timeParts[1]) ? timeParts[1] : 0
    );
  }

  function renderDashPatientList() {
    const list = $("#dash-patient-list");
    if (!list) return;
    const rows = currentPatientList();
    if (!rows.length) {
      list.innerHTML = `<p class="clinic-empty">${dashState.filter === "today" ? "No visits scheduled today." : "No upcoming visits."}</p>`;
      dashState.selectedId = null;
      renderDashBrief();
      return;
    }
    if (!rows.some((b) => b.id === dashState.selectedId)) {
      dashState.selectedId = rows[0]?.id || null;
    }
    list.innerHTML = rows
      .map((b) => {
        const active = b.id === dashState.selectedId ? " is-active" : "";
        const initials = initialsFrom(b.email, b.name);
        const visitLabel =
          dashState.filter === "upcoming" && b.date
            ? formatIsoDate(b.date)
            : visitTypeLabel(b);
        const tone = dashState.filter === "upcoming" && b.date ? "muted" : visitTypeTone(b);
        const whenLabel = formatBookingWhen(b.date, b.time);
        const reportId = String(b.assessment_id || "").trim();
        const contactBits = [b.email, b.phone].filter(Boolean).map(String);
        return `
          <div class="clinic-patient-row${active}" role="listitem" data-dash-patient="${escapeHtml(b.id)}">
            <div class="clinic-patient-main">
              <span class="clinic-patient-avatar" aria-hidden="true">${escapeHtml(initials)}</span>
              <span class="clinic-patient-text">
                <span class="clinic-patient-top">
                  <span class="clinic-patient-name">${escapeHtml(b.name || "Patient")}</span>
                  <span class="clinic-time-pill">${escapeHtml(formatDashTime(b.time))}</span>
                </span>
                <span class="clinic-patient-sub tone-${tone}">${escapeHtml(visitLabel)}</span>
              </span>
            </div>
            <div class="clinic-patient-meta">
              ${
                contactBits.length
                  ? `<span class="clinic-patient-contact">${escapeHtml(contactBits.join(" · "))}</span>`
                  : `<span class="clinic-patient-contact is-empty">No contact on file</span>`
              }
              <span class="clinic-patient-meta-actions">
                <span class="clinic-patient-checked" title="${escapeHtml(whenLabel)}">
                  <span class="clinic-patient-checked-label">Checked</span>
                  <time class="clinic-patient-checked-when">${escapeHtml(whenLabel)}</time>
                </span>
                ${
                  reportId
                    ? `<button type="button" class="clinic-patient-report" data-open-report="${escapeHtml(reportId)}">View report</button>`
                    : `<span class="clinic-patient-report is-disabled">No report</span>`
                }
              </span>
            </div>
          </div>`;
      })
      .join("");

    const activeRow = list.querySelector(".clinic-patient-row.is-active");
    if (activeRow) {
      requestAnimationFrame(() => {
        activeRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    }
    renderDashBrief();
  }

  function formatCountdown(mins) {
    if (mins == null || !Number.isFinite(mins)) return { label: "—", cls: "" };
    if (mins <= 0) return { label: "Now / due", cls: "is-now" };
    if (mins < 60) return { label: `In ${mins} min`, cls: mins <= 15 ? "is-soon" : "" };
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return { label: m ? `In ${h}h ${m}m` : `In ${h}h`, cls: "" };
  }

  function renderDashBrief() {
    const body = $("#dash-brief-body");
    if (!body) return;

    const isToday = dashState.filter === "today";
    const rows = currentPatientList();

    if (!rows.length) {
      body.innerHTML = `<p class="clinic-empty">${
        isToday ? "No visits on today's chair." : "No upcoming visits scheduled."
      }</p>`;
      return;
    }

    const now = new Date();
    const timed = rows
      .map((b) => ({ b, dt: bookingDateTime(b) }))
      .sort((a, b) => {
        if (!a.dt && !b.dt) return 0;
        if (!a.dt) return 1;
        if (!b.dt) return -1;
        return a.dt - b.dt;
      });

    const remaining = isToday
      ? timed.filter(({ dt }) => !dt || dt.getTime() >= now.getTime() - 15 * 60 * 1000)
      : timed;
    const next = remaining[0] || timed[0];
    const withAssess = rows.filter((b) => String(b.assessment_id || "").trim()).length;
    const walkins = Math.max(0, rows.length - withAssess);
    const clinicBooked = rows.filter((b) => b?.source === "admin").length;
    const assessmentVisits = Math.max(0, rows.length - clinicBooked);
    const noAssessList = rows.filter((b) => !String(b.assessment_id || "").trim());

    const first = timed.find((x) => x.dt)?.dt;
    const last = [...timed].reverse().find((x) => x.dt)?.dt;
    const spanLabel =
      first && last
        ? `${formatDashTime(
            `${String(first.getHours()).padStart(2, "0")}:${String(first.getMinutes()).padStart(2, "0")}`
          )} → ${formatDashTime(
            `${String(last.getHours()).padStart(2, "0")}:${String(last.getMinutes()).padStart(2, "0")}`
          )}`
        : "—";

    const mins =
      next?.dt != null ? Math.round((next.dt.getTime() - now.getTime()) / 60000) : null;
    const countdown = formatCountdown(isToday ? mins : null);
    const nextName = next?.b?.name || "Patient";
    const nextTime = formatDashTime(next?.b?.time) || "—";
    const nextVisit = visitTypeLabel(next?.b || {});
    const nextHasReport = Boolean(String(next?.b?.assessment_id || "").trim());
    const prepOk = noAssessList.length === 0;

    body.innerHTML = `
      <div class="brief-sheet">
        <div class="brief-col brief-col-main">
          <div class="brief-next">
            <div class="brief-next-copy">
              <span class="brief-next-label">${isToday ? "Next up" : "First upcoming"}</span>
              <div class="brief-next-main">
                <strong class="brief-next-name">${escapeHtml(nextName)}</strong>
                <span class="brief-next-time">${escapeHtml(nextTime)}</span>
              </div>
              <p class="brief-next-meta">${escapeHtml(nextVisit)} · ${
                nextHasReport ? "Report ready" : "No assessment"
              }</p>
              ${
                isToday && countdown.label !== "—"
                  ? `<span class="brief-countdown ${countdown.cls}">${escapeHtml(countdown.label)}</span>`
                  : ""
              }
            </div>
            <div class="brief-next-art" aria-hidden="true">
              <img src="/static/assets/day-brief.png?v=7" alt="" width="775" height="896" />
            </div>
          </div>
          <div class="brief-stats" role="list">
            <div class="brief-stat" role="listitem">
              <span class="brief-stat-value">${remaining.length}</span>
              <span class="brief-stat-label">${isToday ? "Left today" : "Scheduled"}</span>
            </div>
            <div class="brief-stat" role="listitem">
              <span class="brief-stat-value">${withAssess}</span>
              <span class="brief-stat-label">With report</span>
            </div>
            <div class="brief-stat" role="listitem">
              <span class="brief-stat-value">${walkins}</span>
              <span class="brief-stat-label">No assessment</span>
            </div>
          </div>
        </div>

        <div class="brief-col brief-col-info">
          <div class="brief-insights" role="list">
            <div class="brief-insight" role="listitem">
              <span class="brief-insight-label">Schedule</span>
              <strong class="brief-insight-value">${escapeHtml(spanLabel)}</strong>
            </div>
            <div class="brief-insight" role="listitem">
              <span class="brief-insight-label">Visit mix</span>
              <div class="brief-insight-split">
                <span><strong>${assessmentVisits}</strong> assess</span>
                <span><strong>${clinicBooked}</strong> clinic</span>
              </div>
            </div>
            <div class="brief-insight ${prepOk ? "is-ok" : "is-warn"}" role="listitem">
              <span class="brief-insight-label">Prep</span>
              <strong class="brief-insight-value">${
                prepOk
                  ? "All linked"
                  : `${noAssessList.length} need exam`
              }</strong>
              <span class="brief-insight-note">${
                prepOk
                  ? "Every visit has an assessment"
                  : "Missing digital assessment"
              }</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderTopConcernsChart(rows) {
    const el = $("#dash-concerns-chart");
    const totalEl = $("#dash-concerns-total");
    if (!el) return;

    const list = (Array.isArray(rows) ? rows : [])
      .map((r) => ({
        label: prettyLabel(r.label || r.key || "Other"),
        count: Number(r.count) || 0,
      }))
      .filter((r) => r.count > 0)
      .slice(0, 6);

    const total = list.reduce((sum, r) => sum + r.count, 0);
    if (totalEl) totalEl.textContent = total ? `${total} findings` : "";

    if (!list.length || !total) {
      el.innerHTML = `<p class="clinic-empty">No concern data yet.</p>`;
      return;
    }

    const colors = ["#204088", "#009898", "#2c52a8", "#1ab0b0", "#5b7fbf", "#7ec8c4"];
    const cx = 80;
    const cy = 80;
    const r = 68;
    let angle = 0;

    const polar = (deg) => {
      const rad = ((deg - 90) * Math.PI) / 180;
      return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
    };

    const slices = list.map((item, i) => {
      const share = item.count / total;
      const sweep = share * 360;
      const start = angle;
      const end = angle + sweep;
      angle = end;
      let path = "";
      if (sweep >= 359.9) {
        path = `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r} Z`;
      } else {
        const [x1, y1] = polar(start);
        const [x2, y2] = polar(end);
        const large = sweep > 180 ? 1 : 0;
        path = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
      }
      return {
        path,
        color: colors[i % colors.length],
        label: item.label,
        count: item.count,
        pct: Math.round(share * 100),
      };
    });

    el.innerHTML = `
      <div class="concerns-chart-layout">
        <div class="concerns-pie-wrap">
          <svg class="concerns-pie" viewBox="0 0 160 160" role="img" aria-label="Top concerns distribution">
            <circle cx="80" cy="80" r="68" fill="rgba(32,64,136,0.06)" />
            ${slices.map((s) => `<path d="${s.path}" fill="${s.color}" stroke="#fff" stroke-width="2" />`).join("")}
            <circle cx="80" cy="80" r="34" fill="#fff" />
            <text x="80" y="76" text-anchor="middle" class="concerns-pie-count">${total}</text>
            <text x="80" y="94" text-anchor="middle" class="concerns-pie-caption">total</text>
          </svg>
        </div>
        <ul class="concerns-legend">
          ${slices.map((s) => `
            <li class="concerns-legend-item">
              <span class="concerns-legend-dot" style="background:${s.color}" aria-hidden="true"></span>
              <span class="concerns-legend-copy">
                <strong>${escapeHtml(s.label)}</strong>
                <span>${s.count} · ${s.pct}%</span>
              </span>
            </li>`).join("")}
        </ul>
      </div>
    `;
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

  function reportScoreChip(score) {
    if (score == null || !Number.isFinite(Number(score))) {
      return `<span class="ig-score is-none" title="No score"><strong>—</strong></span>`;
    }
    const n = Math.round(Number(score));
    let cls = "attention";
    let label = "Attention";
    if (n >= 90) { cls = "good"; label = "Good"; }
    else if (n >= 75) { cls = "watch"; label = "Watch"; }
    return `<span class="ig-score is-${cls}" title="${n} / 100 · ${label}"><strong>${n}</strong></span>`;
  }

  function formatReportDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso)
        .toLocaleString(undefined, {
          day: "numeric",
          month: "short",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
          hour12: true,
        })
        .replace(/\b(AM|PM)\b/g, (m) => m.toLowerCase());
    } catch {
      return formatWhen(iso) || "—";
    }
  }

  const LIST_PAGE_SIZE = 10;
  const reportsPager = { page: 1, items: [], queryKey: "" };
  const bookingsPager = { page: 1, items: [], queryKey: "" };

  function pageSlice(items, page, size = LIST_PAGE_SIZE) {
    const total = Array.isArray(items) ? items.length : 0;
    const pages = Math.max(1, Math.ceil(total / size) || 1);
    const p = Math.min(Math.max(1, Number(page) || 1), pages);
    const start = total ? (p - 1) * size : 0;
    const end = Math.min(start + size, total);
    return { page: p, pages, total, start, end, slice: total ? items.slice(start, end) : [] };
  }

  function pageNumbers(current, totalPages) {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const pages = new Set([1, totalPages, current, current - 1, current + 1, current - 2, current + 2]);
    const sorted = [...pages].filter((p) => p >= 1 && p <= totalPages).sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    sorted.forEach((p) => {
      if (prev && p - prev > 1) out.push("…");
      out.push(p);
      prev = p;
    });
    return out;
  }

  function updateSheetPager(kind, info) {
    const pager = $(`#${kind}-pager`);
    const meta = $(`#${kind}-pager-meta`);
    const pagesEl = $(`#${kind}-pager-pages`);
    const prev = $(`#${kind}-pager-prev`);
    const next = $(`#${kind}-pager-next`);
    if (!pager) return;
    if (!info || info.total <= 0) {
      pager.hidden = true;
      pager.classList.remove("is-multipage");
      return;
    }
    pager.hidden = false;
    const multipage = info.pages > 1;
    pager.classList.toggle("is-multipage", multipage);
    if (meta) {
      meta.textContent =
        info.total === 1
          ? "1 result"
          : multipage
            ? `${info.start + 1}–${info.end} of ${info.total}`
            : `${info.total} results`;
    }
    if (pagesEl) {
      if (!multipage) {
        pagesEl.innerHTML = "";
      } else {
        pagesEl.innerHTML = pageNumbers(info.page, info.pages)
          .map((p) => {
            if (p === "…") return `<span class="sheet-pager-ellipsis" aria-hidden="true">…</span>`;
            const active = p === info.page ? " is-active" : "";
            return `<button type="button" class="sheet-pager-num${active}" data-pager="${kind}" data-page="${p}" aria-label="Page ${p}" ${
              p === info.page ? 'aria-current="page"' : ""
            }>${p}</button>`;
          })
          .join("");
      }
    }
    if (prev) prev.disabled = !multipage || info.page <= 1;
    if (next) next.disabled = !multipage || info.page >= info.pages;
  }

  function renderReportsPage() {
    const list = $("#reports-list");
    if (!list) return;
    const items = reportsPager.items;
    if (!items.length) {
      list.innerHTML = emptyState("No reports found matching your search.");
      updateSheetPager("reports", null);
      return;
    }
    const info = pageSlice(items, reportsPager.page);
    reportsPager.page = info.page;
    list.innerHTML = info.slice.map((r, i) => reportRowHtml(r, info.start + i + 1)).join("");
    updateSheetPager("reports", info);
    hydrateReportThumbs(list);
    prefetchReportThumbs(info.slice);
  }

  function bookingRowHtml(b, index) {
    const initials = initialsFrom(b.email, b.name);
    const isTreated = bookingIsTreated(b);
    const isPending = b.status === "pending";
    const isConfirmedActive = (b.status === "confirmed" || b.status === "approved") && !isTreated;
    const canCancel = isConfirmedActive;
    const canToggleTreated = (b.status === "confirmed" || b.status === "approved") || isTreated;
    const canRebook = b.status === "cancelled" || b.status === "rejected" || isTreated;
    const whenLabel = formatBookingWhen(b.date, b.time);
    const reportId = String(b.assessment_id || "").trim();
    const actions = [];
    if (isPending) {
      actions.push(`
        <button type="button" class="booking-approve-btn"
          data-approve="${escapeHtml(b.id)}"
          data-approve-name="${escapeHtml(b.name || "")}"
          data-approve-email="${escapeHtml(b.email || "")}"
          data-approve-when="${escapeHtml(whenLabel)}"
          aria-label="Approve appointment">
          Approve
        </button>`);
      actions.push(`
        <button type="button" class="booking-reject-btn"
          data-reject="${escapeHtml(b.id)}"
          data-reject-name="${escapeHtml(b.name || "")}"
          data-reject-email="${escapeHtml(b.email || "")}"
          data-reject-when="${escapeHtml(whenLabel)}"
          aria-label="Reject appointment">
          Reject
        </button>`);
    }
    if (canToggleTreated) {
      actions.push(`
        <button type="button" class="booking-treat-btn${isTreated ? " is-treated" : ""}"
          data-toggle-treated="${escapeHtml(b.id)}"
          data-treated="${isTreated ? "1" : "0"}"
          data-treat-name="${escapeHtml(b.name || "")}"
          data-treat-email="${escapeHtml(b.email || "")}"
          data-treat-when="${escapeHtml(whenLabel)}"
          aria-label="${isTreated ? "Mark as not treated" : "Mark as treated"}">
          ${isTreated ? "Not treated" : "Mark treated"}
        </button>`);
    }
    if (reportId) {
      actions.push(`
        <button type="button" class="booking-report-btn" data-view-report="${escapeHtml(reportId)}"
          data-booking-id="${escapeHtml(b.id)}"
          data-treated="${isTreated ? "1" : "0"}"
          data-treat-name="${escapeHtml(b.name || "")}"
          data-treat-email="${escapeHtml(b.email || "")}"
          data-treat-when="${escapeHtml(whenLabel)}"
          data-booking-status="${escapeHtml(b.status || "")}"
          data-booking-date="${escapeHtml(b.date || "")}"
          data-booking-time="${escapeHtml(b.time || "")}"
          aria-label="View report">
          View report
        </button>`);
    }
    if (canCancel) {
      actions.push(`
        <button type="button" class="booking-cancel-btn"
          data-cancel="${escapeHtml(b.id)}"
          data-cancel-name="${escapeHtml(b.name || "")}"
          data-cancel-email="${escapeHtml(b.email || "")}"
          data-cancel-when="${escapeHtml(whenLabel)}"
          aria-label="Cancel appointment">
          Cancel
        </button>`);
    } else if (canRebook && !isPending) {
      actions.push(`
        <button type="button" class="booking-rebook-btn"
          data-book-again
          data-name="${escapeHtml(b.name || "")}"
          data-email="${escapeHtml(b.email || "")}"
          data-phone="${escapeHtml(b.phone || "")}"
          data-assessment="${escapeHtml(reportId)}"
          data-note="${escapeHtml(isTreated ? "Follow-up booking after treated visit." : "Rebooked after a cancelled appointment.")}"
          aria-label="Book again">
          Book again
        </button>`);
    }
    return `
      <div class="booking-row" role="listitem" data-booking="${escapeHtml(b.id)}">
        <span class="booking-col booking-col-index">${index}</span>
        <span class="booking-col booking-col-patient">
          <span class="booking-avatar" aria-hidden="true">${escapeHtml(initials)}</span>
          <span class="booking-identity-text">
            <span class="booking-name">${escapeHtml(b.name || "—")}</span>
            <span class="booking-email">${escapeHtml(b.email || "—")}</span>
            <span class="booking-phone">${escapeHtml(b.phone || "—")}</span>
            ${(b.gender || b.age || b.city) ? `<span class="booking-phone" style="opacity:0.8;font-size:0.72rem;margin-top:2px;color:var(--navy-mid);">${escapeHtml([b.gender, b.age ? `${b.age} yrs` : null, b.city].filter(Boolean).join(" • "))}</span>` : ""}
          </span>
        </span>
        <span class="booking-col booking-col-source">${sourceBadge(b.source)}</span>
        <span class="booking-col booking-col-status">${statusBadge(b.status, isTreated)}</span>
        <span class="booking-col booking-col-when">
          <span class="booking-when">${escapeHtml(whenLabel)}</span>
        </span>
        <span class="booking-col booking-col-action">
          ${actions.length ? `<span class="booking-actions">${actions.join("")}</span>` : `<span class="booking-action-empty">—</span>`}
        </span>
      </div>`;
  }

  function renderBookingsPage() {
    const list = $("#bookings-list");
    if (!list) return;
    const items = bookingsPager.items;
    if (!items.length) {
      list.innerHTML = emptyState("No appointments found.");
      updateSheetPager("bookings", null);
      return;
    }
    const info = pageSlice(items, bookingsPager.page);
    bookingsPager.page = info.page;
    list.innerHTML = info.slice.map((b, i) => bookingRowHtml(b, info.start + i + 1)).join("");
    updateSheetPager("bookings", info);
  }

  function reportRowHtml(r, index) {
    const name = displayName(r.email, r.name);
    const email = r.email || "—";
    const initials = initialsFrom(r.email, r.name);
    const concerns = Array.isArray(r.concerns) ? r.concerns : [];
    const concernCount = concerns.length;
    const photoUrl = String(r.photo_front_url || "").trim();
    const cachedThumb = photoUrl ? thumbCache.get(r.id)?.objectUrl : "";
    const avatar = photoUrl
      ? `<span class="report-avatar report-avatar-photo${cachedThumb ? " is-loaded" : " is-loading"}" data-thumb-id="${escapeHtml(r.id)}" data-thumb-url="${escapeHtml(photoUrl)}" aria-hidden="true"><img alt="" decoding="async"${cachedThumb ? ` src="${escapeHtml(cachedThumb)}"` : ""} /></span>`
      : `<span class="report-avatar" aria-hidden="true">${escapeHtml(initials)}</span>`;
    const apptDate = String(r.appointment_date || "").trim();
    const apptTime = String(r.appointment_time || "").trim();
    const apptLabel = apptDate
      ? formatBookingWhen(apptDate, apptTime)
      : "None";

    return `
      <button type="button" class="report-row" data-report="${escapeHtml(r.id)}" role="listitem">
        <span class="report-col report-col-index">${index}</span>
        <span class="report-col report-col-identity">
          ${avatar}
          <span class="report-identity-text">
            <span class="report-name">${escapeHtml(name)}</span>
            <span class="report-email">${escapeHtml(email)}</span>
            ${r.phone ? `<span class="report-phone">${escapeHtml(r.phone)}</span>` : `<span class="report-phone is-empty">No phone</span>`}
            ${(r.gender || r.age || r.city) ? `<span class="report-phone" style="opacity:0.8;font-size:0.72rem;margin-top:2px;color:var(--navy-mid);">${escapeHtml([r.gender, r.age ? `${r.age} yrs` : null, r.city].filter(Boolean).join(" • "))}</span>` : ""}
          </span>
        </span>
        <span class="report-col report-col-score">${reportScoreChip(r.overall_score)}</span>
        <span class="report-col report-col-concerns">
          <span class="report-concern-count">${concernCount}</span>
        </span>
        <span class="report-col report-col-appt">
          <span class="report-appt${apptDate ? "" : " is-none"}">${escapeHtml(apptLabel)}</span>
        </span>
        <span class="report-col report-col-date">
          <span class="report-date">${escapeHtml(formatReportDate(r.created_at))}</span>
        </span>
        <span class="report-col report-col-action" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </span>
      </button>
    `;
  }

  /* Status badge for appointments */
  function statusBadge(status, treated = false) {
    if (status === "pending") return `<span class="ig-pill is-warning">Pending Approval</span>`;
    if (status === "rejected") return `<span class="ig-pill is-danger">Rejected</span>`;
    if (status === "cancelled") return `<span class="ig-pill is-danger">Cancelled</span>`;
    if (treated || status === "treated") return `<span class="ig-pill is-teal">Treated</span>`;
    if (status === "confirmed" || status === "approved") return `<span class="ig-pill is-ok">Approved</span>`;
    return `<span class="ig-pill is-muted">${escapeHtml(status)}</span>`;
  }

  function bookingIsTreated(b) {
    if (!b) return false;
    if (b.treated === true || b.treated === "true" || b.treated === 1) return true;
    if (b.treated === false || b.treated === "false" || b.treated === 0) return false;
    return String(b.note || "").trim().toUpperCase().startsWith("[TREATED]");
  }

  /* Source badge */
  function sourceBadge(source) {
    if (source === "admin") return `<span class="ig-pill is-navy">Admin</span>`;
    return `<span class="ig-pill is-teal">${escapeHtml(prettyLabel(source || "Patient"))}</span>`;
  }

  /* ── API wrapper ────────────────────────────────────── */
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const attempts = options.retry === false ? 1 : 3;
    let lastErr = null;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        const res  = await fetch(path, { ...options, headers });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) {
          token = "";
          safeSetToken("");
          setLoggedIn(false);
          throw new Error("Session expired. Please sign in again.");
        }
        if (!res.ok) {
          const retryable = res.status === 500 || res.status === 502 || res.status === 503 || res.status === 504;
          let detail = `Request failed (${res.status})`;
          if (typeof data.detail === "string") detail = data.detail;
          else if (Array.isArray(data.detail) && data.detail.length) detail = String(data.detail[0]?.msg || detail);
          else if (typeof data.message === "string") detail = data.message;
          if (retryable && attempt < attempts) {
            await new Promise((r) => setTimeout(r, 200 * attempt));
            continue;
          }
          throw new Error(detail);
        }
        return data;
      } catch (e) {
        lastErr = e;
        if (e?.message?.includes("Session expired")) throw e;
        if (attempt >= attempts) throw e;
        await new Promise((r) => setTimeout(r, 200 * attempt));
      }
    }
    throw lastErr || new Error("Request failed");
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
      if (active === "reports")      loadReports({ soft: true });
      if (active === "appointments") loadBookings();
      if (active === "hours")        loadSchedules();
      if (openReportId && !$("#patient-modal")?.hidden) {
        refreshOpenReport(true);
      }
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
    if (name === "reports") {
      const list = $("#reports-list");
      const hasRows = !!list?.querySelector(".report-row, .report-row-skel");
      loadReports({ soft: hasRows });
    }
    if (name === "appointments") loadBookings();
    if (name === "hours")        loadSchedules();
    if (name === "book") {
      const status = $("#walkin-status");
      const newBtn = $("#w-new-booking-btn");
      if ((newBtn && !newBtn.hidden) || (status && status.textContent.trim().length > 0)) {
        resetWalkinBookingForm();
      } else {
        initWalkinBookingUi();
      }
    }
  }

  function refreshAll() {
    const active = $(".tab.is-active")?.dataset.tab || "dashboard";
    showTab(active);
  }

  /* ── Dashboard ──────────────────────────────────────── */
  function setHeroTrend(el, pct) {
    if (!el) return;
    let n = Number(pct);
    if (!Number.isFinite(n)) n = 0;
    const abs = Math.min(999, Math.abs(Math.round(n)));
    const pctEl = el.querySelector(".clinic-pill-trend-pct");
    if (pctEl) pctEl.textContent = `${abs}%`;
    el.classList.remove("is-up", "is-down", "is-flat");
    if (n > 0) el.classList.add("is-up");
    else if (n < 0) el.classList.add("is-down");
    else el.classList.add("is-flat");
    el.hidden = false;
    el.removeAttribute("hidden");
    el.title = n > 0 ? `Up ${abs}% vs last week` : n < 0 ? `Down ${abs}% vs last week` : "No change vs last week";
  }

  async function loadStats() {
    try {
      const data = await api("/admin/api/stats");
      const todayCount = Number(data.today_count) || 0;
      const bookings = Number(data.booking_count) || 0;
      const assessments = Number(data.assessment_count) || 0;
      const avg = data.avg_smile_score != null ? Number(data.avg_smile_score) : null;

      if ($("#dash-today-count")) $("#dash-today-count").textContent = todayCount;
      if ($("#dash-booking-count")) $("#dash-booking-count").textContent = bookings;
      if ($("#dash-test-count")) $("#dash-test-count").textContent = assessments;
      setHeroTrend($("#dash-booking-trend"), data.booking_change_pct);
      setHeroTrend($("#dash-test-trend"), data.assessment_change_pct);
      if ($("#dash-avg-score")) {
        $("#dash-avg-score").textContent = avg != null && Number.isFinite(avg) ? `Avg ${avg}` : "Avg —";
      }

      dashState.today = Array.isArray(data.today_visits) ? data.today_visits : [];
      dashState.upcoming = Array.isArray(data.upcoming) ? data.upcoming : [];
      dashState.notifications = buildNotifications({
        today: dashState.today,
        upcoming: dashState.upcoming,
        assessmentCount: assessments,
      });

      renderTopConcernsChart(data.top_concerns || []);
      renderScoreMix($("#dash-score-mix"), data.score_distribution || []);
      renderDashPatientList();
      renderNotifications();
    } catch (e) {
      console.warn("[admin] loadStats:", e);
    }
  }

  /* ── Stale-while-revalidate cache ────────────────────── */
  function detailFingerprint(data) {
    const r = data?.report || {};
    const bookings = data?.bookings || [];
    return JSON.stringify({
      id: r.id,
      overall_score: r.overall_score,
      email: r.email,
      phone: r.phone,
      concerns: r.concerns,
      treatments: r.treatments,
      findings: r.findings,
      category_scores: r.category_scores,
      photo_front_path: r.photo_front_path,
      photo_left_path: r.photo_left_path,
      photo_right_path: r.photo_right_path,
      email_sent_at: r.email_sent_at,
      bookings: bookings.map((b) => [b.id, b.status, b.date, b.time, b.name]),
    });
  }

  function listFingerprint(items) {
    return JSON.stringify(
      (items || []).map((r) => [
        r.id,
        r.overall_score,
        r.email,
        r.phone,
        r.name,
        r.created_at,
        r.appointment_date,
        r.appointment_time,
        Array.isArray(r.concerns) ? r.concerns.length : 0,
        // Presence only — signed URLs change every fetch and must not force remounts.
        Boolean(r.photo_front_url),
      ])
    );
  }

  function loadDetailCacheFromSession() {
    try {
      const raw = sessionStorage.getItem(DETAIL_CACHE_STORAGE);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return;
      Object.entries(parsed).forEach(([id, entry]) => {
        if (entry?.data) {
          detailCache.set(id, {
            data: entry.data,
            fingerprint: entry.fingerprint || detailFingerprint(entry.data),
            at: entry.at || Date.now(),
          });
        }
      });
    } catch { /* ignore */ }
  }

  function persistDetailCache() {
    try {
      const obj = {};
      [...detailCache.entries()]
        .sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
        .slice(0, DETAIL_CACHE_MAX)
        .forEach(([id, entry]) => {
          obj[id] = {
            data: entry.data,
            fingerprint: entry.fingerprint,
            at: entry.at,
          };
        });
      sessionStorage.setItem(DETAIL_CACHE_STORAGE, JSON.stringify(obj));
    } catch { /* ignore quota */ }
  }

  function getCachedDetail(id) {
    return detailCache.get(id) || null;
  }

  function setCachedDetail(id, data) {
    const entry = {
      data,
      fingerprint: detailFingerprint(data),
      at: Date.now(),
    };
    detailCache.set(id, entry);
    persistDetailCache();
    return entry;
  }

  function warmPhotos(photos, reportId = "") {
    const id = String(reportId || openReportId || "").trim();
    Object.entries(photos || {}).forEach(([slot, url]) => {
      if (!url) return;
      const key = photoCacheKey(id, slot);
      if (key && !thumbCache.has(key) && !thumbInflight.has(key)) {
        resolveThumbUrl(key, url).catch(() => {});
      } else if (!key) {
        const img = new Image();
        img.decoding = "async";
        img.src = url;
      }
    });
  }

  function pruneThumbCache() {
    if (thumbCache.size <= THUMB_CACHE_MAX) return;
    const oldest = [...thumbCache.entries()]
      .sort((a, b) => (a[1].at || 0) - (b[1].at || 0))
      .slice(0, thumbCache.size - THUMB_CACHE_MAX);
    oldest.forEach(([id, entry]) => {
      if (entry?.objectUrl) URL.revokeObjectURL(entry.objectUrl);
      thumbCache.delete(id);
    });
  }

  function resolveThumbUrl(id, sourceUrl) {
    const cleanId = String(id || "").trim();
    const cleanUrl = String(sourceUrl || "").trim();
    if (!cleanId || !cleanUrl) return Promise.resolve("");

    const hit = thumbCache.get(cleanId);
    if (hit?.objectUrl) {
      hit.at = Date.now();
      return Promise.resolve(hit.objectUrl);
    }
    if (thumbInflight.has(cleanId)) return thumbInflight.get(cleanId);

    const p = fetch(cleanUrl, { mode: "cors", credentials: "omit", cache: "force-cache" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`thumb ${res.status}`);
        const blob = await res.blob();
        if (!blob || !String(blob.type || "").startsWith("image/")) {
          throw new Error("invalid thumb");
        }
        const objectUrl = URL.createObjectURL(blob);
        const prev = thumbCache.get(cleanId);
        if (prev?.objectUrl && prev.objectUrl !== objectUrl) {
          URL.revokeObjectURL(prev.objectUrl);
        }
        thumbCache.set(cleanId, { objectUrl, sourceUrl: cleanUrl, at: Date.now() });
        pruneThumbCache();
        return objectUrl;
      })
      .catch(() => {
        // Fall back to signed URL if blob caching is blocked.
        thumbCache.set(cleanId, { objectUrl: cleanUrl, sourceUrl: cleanUrl, at: Date.now() });
        return cleanUrl;
      })
      .finally(() => {
        thumbInflight.delete(cleanId);
      });

    thumbInflight.set(cleanId, p);
    return p;
  }

  function hydrateReportThumbs(root = $("#reports-list")) {
    if (!root) return;
    const nodes = [...root.querySelectorAll(".report-avatar-photo[data-thumb-id]")];
    nodes.forEach((el, i) => {
      const id = el.dataset.thumbId;
      const url = el.dataset.thumbUrl;
      const img = el.querySelector("img");
      if (!id || !url || !img) return;

      // Already painted from cache — don't re-trigger shimmer/fade.
      if (el.classList.contains("is-loaded") && img.complete && img.naturalWidth) {
        return;
      }

      const apply = (src) => {
        if (!src) {
          el.classList.remove("is-loading");
          el.classList.add("is-error");
          return;
        }
        const reveal = () => {
          el.classList.remove("is-loading", "is-error");
          el.classList.add("is-loaded");
        };
        if (img.getAttribute("src") === src && img.complete && img.naturalWidth) {
          reveal();
          return;
        }
        img.onload = reveal;
        img.onerror = () => {
          el.classList.remove("is-loading", "is-loaded");
          el.classList.add("is-error");
        };
        if (img.getAttribute("src") !== src) {
          const instant = thumbCache.has(id) || el.classList.contains("is-loaded");
          if (!instant) {
            el.classList.add("is-loading");
            el.classList.remove("is-loaded", "is-error");
          }
          img.src = src;
          if (instant && img.complete && img.naturalWidth) reveal();
        } else if (img.complete) {
          reveal();
        }
      };

      const cached = thumbCache.get(id)?.objectUrl;
      if (cached) {
        apply(cached);
        return;
      }

      const delay = Math.min(i * 28, 320);
      window.setTimeout(() => {
        resolveThumbUrl(id, url).then(apply);
      }, delay);
    });
  }

  function prefetchReportThumbs(items) {
    (items || []).slice(0, 24).forEach((r, i) => {
      const url = String(r?.photo_front_url || "").trim();
      if (!r?.id || !url || thumbCache.has(r.id) || thumbInflight.has(r.id)) return;
      window.setTimeout(() => {
        resolveThumbUrl(r.id, url).catch(() => {});
      }, i * 20);
    });
  }

  function fetchReportDetail(id, { force = false } = {}) {
    if (!force && detailInflight.has(id)) return detailInflight.get(id);
    const p = api(`/admin/api/reports/${id}`)
      .then((data) => {
        setCachedDetail(id, data);
        warmPhotos(data.photos, id);
        return data;
      })
      .finally(() => {
        detailInflight.delete(id);
      });
    detailInflight.set(id, p);
    return p;
  }

  async function refreshOpenReport(soft = false) {
    const id = openReportId;
    if (!id) return;
    try {
      const prev = getCachedDetail(id);
      const data = await fetchReportDetail(id, { force: true });
      if (openReportId !== id) return;
      const nextFp = detailFingerprint(data);
      if (soft && prev && prev.fingerprint === nextFp) {
        // Keep existing signed photo URLs in the open view; refresh cache only.
        return;
      }
      renderPatientDetail(data, { animateBars: !soft });
    } catch (e) {
      if (!soft) console.warn("[admin] refreshOpenReport:", e);
    }
  }

  loadDetailCacheFromSession();

  function reportBandKey(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return "none";
    if (n >= 90) return "good";
    if (n >= 75) return "watch";
    return "attention";
  }

  function reportInDateFilter(iso, filter) {
    if (!filter || filter === "all") return true;
    if (!iso) return false;
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return false;
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (filter === "today") return t >= startOfToday;
    if (filter === "7d") return t >= startOfToday - 6 * 24 * 60 * 60 * 1000;
    if (filter === "30d") return t >= startOfToday - 29 * 24 * 60 * 60 * 1000;
    return true;
  }

  function sortReportItems(items, sort, order) {
    const dir = order === "asc" ? 1 : -1;
    const list = [...items];
    list.sort((a, b) => {
      if (sort === "email") {
        const ae = displayName(a.email, a.name).toLowerCase();
        const be = displayName(b.email, b.name).toLowerCase();
        return ae < be ? -dir : ae > be ? dir : 0;
      }
      if (sort === "overall_score") {
        const as = Number(a.overall_score);
        const bs = Number(b.overall_score);
        const av = Number.isFinite(as) ? as : -1;
        const bv = Number.isFinite(bs) ? bs : -1;
        return (av - bv) * dir;
      }
      const at = new Date(a.created_at || 0).getTime() || 0;
      const bt = new Date(b.created_at || 0).getTime() || 0;
      return (at - bt) * dir;
    });
    return list;
  }

  function reportsSkeletonHtml(count = 7) {
    return Array.from({ length: count }, () => `
      <div class="report-row report-row-skel" aria-hidden="true">
        <span class="report-col report-col-index"><span class="skel-block skel-num"></span></span>
        <span class="report-col report-col-identity">
          <span class="skel-block skel-avatar"></span>
          <span class="report-identity-text">
            <span class="skel-block skel-line skel-line-lg"></span>
            <span class="skel-block skel-line skel-line-md"></span>
            <span class="skel-block skel-line skel-line-sm"></span>
          </span>
        </span>
        <span class="report-col report-col-score"><span class="skel-block skel-chip"></span></span>
        <span class="report-col report-col-concerns"><span class="skel-block skel-num"></span></span>
        <span class="report-col report-col-appt"><span class="skel-block skel-line skel-line-md"></span></span>
        <span class="report-col report-col-date"><span class="skel-block skel-line skel-line-md"></span></span>
        <span class="report-col report-col-action"><span class="skel-block skel-chevron"></span></span>
      </div>
    `).join("");
  }

  /* ── Reports ────────────────────────────────────────── */
  /*
   * Two-tier caching strategy for the reports list:
   *
   *  Tier 1 – rawReportsCache: keyed by the search query "q" only.
   *            Stores the full unfiltered/unsorted list returned by the API.
   *            An API call is triggered ONLY when "q" changes.
   *
   *  Tier 2 – apply band, dateFilter, and sort entirely client-side from
   *            the raw cache. Zero network round-trips for those controls.
   *
   * This makes sorting, status filters, and date filters feel instantaneous.
   */
  const rawReportsCache = new Map(); // rawKey -> { items, at }

  function applyReportsFilters(rawItems, { band, dateFilter, sort, order }) {
    let items = Array.isArray(rawItems) ? rawItems : [];
    if (band && band !== "all") {
      items = items.filter((r) => reportBandKey(r.overall_score) === band);
    }
    if (dateFilter && dateFilter !== "all") {
      items = items.filter((r) => reportInDateFilter(r.created_at, dateFilter));
    }
    items = sortReportItems(items, sort || "created_at", order || "desc");
    return items;
  }

  async function loadReports(opts = {}) {
    const soft = !!opts.soft;
    const q = $("#reports-q").value.trim();
    const [sort, order] = ($("#reports-sort").value || "created_at:desc").split(":");
    const band = $("#reports-band")?.value || "all";
    const dateFilter = $("#reports-date")?.value || "all";

    // The view key encodes every dimension that affects the rendered list.
    const viewKey = `q=${q}|band=${band}|date=${dateFilter}|sort=${sort}:${order}`;
    // The raw key is used only for the API call – excludes client-side params.
    const rawKey = q; // keyed by search term only (client-side filters excluded)

    const list = $("#reports-list");
    if (!list) return;

    const viewChanged = reportsPager.queryKey !== viewKey;

    // ── Fast path: raw data already in memory → filter/sort client-side instantly ──
    const rawCached = rawReportsCache.get(rawKey);
    if (rawCached) {
      const items = applyReportsFilters(rawCached.items, { band, dateFilter, sort, order });
      if (viewChanged) reportsPager.page = 1;
      reportsPager.queryKey = viewKey;
      reportsPager.items = items;
      renderReportsPage();

      if (!soft && Date.now() - rawCached.at > 60_000) {
        // Background-refresh stale data without blocking the UI.
        _refreshRawReports(rawKey, q, { band, dateFilter, sort, order, viewKey });
      }
      return;
    }

    // ── Slow path: first load or search changed → fetch from API ──
    if (!soft) {
      const hasRows = !!list.querySelector(".report-row:not(.report-row-skel)");
      if (!hasRows) {
        list.innerHTML = reportsSkeletonHtml();
        updateSheetPager("reports", null);
      }
    }

    await _refreshRawReports(rawKey, q, { band, dateFilter, sort, order, viewKey });
  }

  async function _refreshRawReports(rawKey, q, { band, dateFilter, sort, order, viewKey }) {
    const list = $("#reports-list");
    if (!list) return;

    const params = new URLSearchParams({ q: q || "", limit: "200" });

    try {
      const data = await api(`/admin/api/reports?${params}`);
      const rawItems = Array.isArray(data.items) ? data.items : [];

      // Store raw unfiltered items.
      rawReportsCache.set(rawKey, { items: rawItems, at: Date.now() });
      // Trim cache to avoid unbounded growth.
      if (rawReportsCache.size > 20) {
        const oldestKey = rawReportsCache.keys().next().value;
        rawReportsCache.delete(oldestKey);
      }

      const items = applyReportsFilters(rawItems, { band, dateFilter, sort, order });
      const viewChanged = reportsPager.queryKey !== viewKey;
      if (viewChanged) reportsPager.page = 1;
      reportsPager.queryKey = viewKey;
      reportsPager.items = items;
      renderReportsPage();

      // Prefetch detail for top rows.
      items.slice(0, 2).forEach((r) => {
        if (r?.id && !getCachedDetail(r.id) && !detailInflight.has(r.id) && detailInflight.size < 2) {
          fetchReportDetail(r.id).catch(() => {});
        }
      });
    } catch (e) {
      if (!list.querySelector(".report-row:not(.report-row-skel)")) {
        list.innerHTML = errorState(e.message);
        updateSheetPager("reports", null);
      }
    }
  }

  /* ── Patient detail modal ───────────────────────────── */
  let photoCarousel = { views: [], index: 0 };

  const CATEGORY_META = [
    { key: "alignment", label: "Alignment" },
    { key: "gum_health", label: "Gum health" },
    { key: "color", label: "Tooth colour" },
    { key: "restorations", label: "Restorations" },
    { key: "missing_teeth", label: "Missing teeth" },
  ];

  function scoreBand(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return { label: "—", cls: "watch" };
    if (n >= 90) return { label: "Good", cls: "good" };
    if (n >= 75) return { label: "Watch", cls: "watch" };
    return { label: "Attention", cls: "attention" };
  }

  function barClass(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return "mid";
    if (n >= 90) return "high";
    if (n >= 75) return "mid";
    if (n >= 50) return "low";
    return "danger";
  }

  function initialsFrom(email, name) {
    const fromName = String(name || "").trim();
    if (fromName) {
      const parts = fromName.split(/\s+/).filter(Boolean);
      if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
      return fromName.slice(0, 2).toUpperCase();
    }
    const local = String(email || "").split("@")[0] || "?";
    const bits = local.replace(/[^a-zA-Z0-9]/g, " ").trim().split(/\s+/).filter(Boolean);
    if (bits.length >= 2) return (bits[0][0] + bits[1][0]).toUpperCase();
    return local.slice(0, 2).toUpperCase();
  }

  function displayName(email, bookingName) {
    if (bookingName && String(bookingName).trim()) return String(bookingName).trim();
    const local = String(email || "").split("@")[0] || "Patient";
    return local.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function closePatientModal() {
    const modal = $("#patient-modal");
    if (modal) modal.hidden = true;
    openReportId = null;
    openReportBooking = null;
    openReportOrigin = null;
    photoCarousel = { views: [], index: 0 };
    syncPmTreatBtn(null);
    const pdfBtn = $("#pm-view-pdf-btn");
    if (pdfBtn) {
      pdfBtn.hidden = true;
      pdfBtn.href = "#";
    }
    const chatBtn = $("#pm-view-chat-btn");
    if (chatBtn) {
      chatBtn.hidden = true;
      chatBtn.onclick = null;
    }
    const headerActions = $("#pm-header-appt-actions");
    if (headerActions) {
      headerActions.innerHTML = "";
      headerActions.hidden = true;
    }
  }

  function closeChatHistoryModal() {
    const modal = $("#patient-chat-modal");
    if (modal) modal.hidden = true;
  }

  function openChatHistoryModal(patientName, history) {
    const modal = $("#patient-chat-modal");
    const body = $("#pcm-body");
    const title = $("#pcm-title");
    if (!modal || !body) return;

    if (title) {
      title.textContent = patientName ? `Queries — ${patientName}` : "Patient Queries";
    }

    if (!Array.isArray(history) || history.length === 0) {
      body.innerHTML = `<p class="pcm-empty">No chatbot history available for this patient.</p>`;
    } else {
      let html = `<div class="pcm-timeline">`;
      history.forEach((msg) => {
        const isUser = msg.role === "user";
        const roleLabel = isUser ? "Patient" : "Chatbot";
        const roleCls = isUser ? "pcm-user" : "pcm-bot";
        const timeStr = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
        html += `
          <div class="pcm-msg ${roleCls}">
            <div class="pcm-msg-header">
              <span class="pcm-role-badge">${escapeHtml(roleLabel)}</span>
              ${timeStr ? `<span class="pcm-time">${escapeHtml(timeStr)}</span>` : ""}
            </div>
            <div class="pcm-msg-content">${escapeHtml(msg.content).replace(/\n/g, "<br>")}</div>
          </div>
        `;
      });
      html += `</div>`;
      body.innerHTML = html;
    }

    modal.hidden = false;
  }

  let openReportBooking = null;
  let openReportOrigin = null; // { source, bookingId } when opened from appointments

  function syncPmTreatBtn(booking) {
    openReportBooking = booking && booking.id ? booking : null;
  }

  function resolveLinkedBooking(bookings, preferredId) {
    const list = Array.isArray(bookings) ? bookings : [];
    if (preferredId) {
      const match = list.find((b) => String(b.id) === String(preferredId));
      if (match) return match;
    }
    return (
      list.find((b) => b.status === "confirmed" && !bookingIsTreated(b)) ||
      list.find((b) => b.status === "confirmed") ||
      list[0] ||
      null
    );
  }

  function focusBookingInAppointments(bookingId) {
    const id = String(bookingId || "").trim();
    closePatientModal();
    showTab("appointments");
    if (!id) return;
    const applyHighlight = () => {
      const row = $(`#bookings-list [data-booking="${CSS.escape(id)}"]`);
      if (!row) return false;
      row.classList.add("is-flash");
      row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      window.setTimeout(() => row.classList.remove("is-flash"), 2200);
      return true;
    };
    if (!applyHighlight()) {
      loadBookings().then(() => {
        window.setTimeout(applyHighlight, 80);
      });
    }
  }

  async function toggleBookingTreated(bookingId, currentlyTreated, { button } = {}) {
    const id = String(bookingId || "").trim();
    if (!id) return;
    const next = !currentlyTreated;
    if (button) {
      button.disabled = true;
      button.textContent = "Saving…";
    }
    try {
      const data = await api(`/admin/api/bookings/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ treated: next }),
      });
      const updated = data || { id, treated: next, status: "confirmed" };
      if (openReportBooking?.id === id) {
        openReportBooking = { ...openReportBooking, treated: !!updated.treated };
        syncPmTreatBtn(openReportBooking);
      }
      const active = $(".tab.is-active")?.dataset.tab;
      if (active === "appointments") loadBookings();
      if (active === "dashboard") loadStats();
      if (openReportId && !$("#patient-modal")?.hidden) {
        const cached = getCachedDetail(openReportId);
        if (cached?.data?.bookings) {
          const nextData = {
            ...cached.data,
            bookings: cached.data.bookings.map((b) =>
              b.id === id ? { ...b, treated: !!updated.treated } : b
            ),
          };
          setCachedDetail(openReportId, nextData);
          renderPatientDetail(nextData, { animateBars: false });
        }
      }
    } catch (ex) {
      alert(ex.message || "Could not update treated status.");
      if (button && openReportBooking?.id === id) syncPmTreatBtn(openReportBooking);
      else if (button) {
        button.disabled = false;
        button.textContent = currentlyTreated ? "Not treated" : "Mark treated";
      }
      throw ex;
    }
  }

  function startBookingFromReport(reportId) {
    const cached = getCachedDetail(reportId);
    const r = cached?.data?.report || {};
    const bookings = cached?.data?.bookings || [];
    const linked =
      bookings.find((b) => b.status === "confirmed" && !bookingIsTreated(b)) ||
      bookings.find((b) => b.status === "confirmed") ||
      bookings[0] ||
      null;
    const name = displayName(r.email, linked?.name || r.name);

    fillWalkinForm({
      name: name && !String(name).includes("@") ? name : "",
      email: r.email || "",
      phone: r.phone || "",
      gender: r.gender || linked?.gender || "",
      age: r.age != null ? r.age : (linked?.age != null ? linked.age : ""),
      city: r.city || linked?.city || "",
      assessmentId: reportId || "",
      note: r.email
        ? `Booked from assessment report${r.overall_score != null ? ` (score ${r.overall_score})` : ""}.`
        : "",
      statusText: "Patient details filled from report. Pick a date and time.",
    });
    closePatientModal();
    showTab("book");
    $("#w-name")?.focus();
  }

  function resetWalkinBookingForm() {
    const form = $("#walkin-form");
    if (form) form.reset();
    if ($("#w-name")) $("#w-name").value = "";
    if ($("#w-email")) $("#w-email").value = "";
    if ($("#w-phone")) $("#w-phone").value = "";
    if ($("#w-gender")) $("#w-gender").value = "";
    if ($("#w-age")) $("#w-age").value = "";
    if ($("#w-city")) $("#w-city").value = "";
    if ($("#w-other-city")) $("#w-other-city").value = "";
    if ($("#w-other-city-wrap")) $("#w-other-city-wrap").hidden = true;
    if ($("#w-city-dropdown-list")) $("#w-city-dropdown-list").hidden = true;
    if ($("#w-note")) $("#w-note").value = "";
    if ($("#w-assessment-id")) $("#w-assessment-id").value = "";

    const status = $("#walkin-status");
    if (status) {
      status.textContent = "";
      status.className = "status";
    }

    const confirmBtn = $("#w-confirm-btn");
    if (confirmBtn) {
      confirmBtn.hidden = false;
      confirmBtn.disabled = false;
    }

    const newBtn = $("#w-new-booking-btn");
    if (newBtn) newBtn.hidden = true;

    initWalkinBookingUi();
  }

  function fillWalkinForm({ name = "", email = "", phone = "", gender = "", age = "", city = "", assessmentId = "", note = "", statusText = "" } = {}) {
    resetWalkinBookingForm();
    if ($("#w-name")) $("#w-name").value = name || "";
    if ($("#w-email")) $("#w-email").value = email || "";
    if ($("#w-phone")) $("#w-phone").value = phone || "";
    if ($("#w-gender")) $("#w-gender").value = gender || "";
    if ($("#w-age")) $("#w-age").value = age != null ? age : "";
    if ($("#w-city")) {
      $("#w-city").value = city || "";
      if (city && !PAKISTANI_CITIES.includes(city)) {
        $("#w-city").value = "Other";
        if ($("#w-other-city")) $("#w-other-city").value = city;
        if ($("#w-other-city-wrap")) $("#w-other-city-wrap").hidden = false;
      }
    }
    if ($("#w-assessment-id")) $("#w-assessment-id").value = assessmentId || "";
    if ($("#w-note")) $("#w-note").value = note || "";
    const status = $("#walkin-status");
    if (status && statusText) {
      status.textContent = statusText;
      status.className = "status is-ok";
    }
  }

  function startBookingAgain(btn) {
    fillWalkinForm({
      name: btn.dataset.name || "",
      email: btn.dataset.email || "",
      phone: btn.dataset.phone || "",
      gender: btn.dataset.gender || "",
      age: btn.dataset.age || "",
      city: btn.dataset.city || "",
      assessmentId: btn.dataset.assessment || "",
      note: btn.dataset.note || "Rebooked after a cancelled appointment.",
      statusText: "Patient details filled. Pick a new date and time.",
    });
    showTab("book");
    $("#w-name")?.focus();
  }

  /* ── Confirm modal (cancel / treat) ─────────────────── */
  let pendingConfirm = null;

  function openCancelConfirm(btn) {
    pendingConfirm = {
      kind: "cancel",
      id: btn.dataset.cancel,
      name: btn.dataset.cancelName || "",
      email: btn.dataset.cancelEmail || "",
      when: btn.dataset.cancelWhen || "",
    };
    showConfirmModal({
      title: "Cancel appointment?",
      copy: "The patient will be emailed that their booking is cancelled.",
      okLabel: "Cancel appointment",
      ghostLabel: "Keep booking",
      okClass: "confirm-btn-danger",
    });
  }

  function openTreatConfirm({ id, name, email, when, currentlyTreated }) {
    pendingConfirm = {
      kind: currentlyTreated ? "untreat" : "treat",
      id,
      name: name || "",
      email: email || "",
      when: when || "",
      currentlyTreated: !!currentlyTreated,
    };
    if (currentlyTreated) {
      showConfirmModal({
        title: "Move back to confirmed?",
        copy: "This visit will leave Treated history and show under Confirmed again.",
        okLabel: "Mark not treated",
        ghostLabel: "Keep treated",
        okClass: "confirm-btn-navy",
      });
    } else {
      showConfirmModal({
        title: "Mark as treated?",
        copy: "This visit moves to Treated history. If the patient books again later, the new appointment appears under Confirmed.",
        okLabel: "Mark as treated",
        ghostLabel: "Keep booking",
        okClass: "confirm-btn-teal",
      });
    }
  }

  function showConfirmModal({ title, copy, okLabel, ghostLabel, okClass }) {
    const modal = $("#confirm-modal");
    const meta = $("#confirm-meta");
    const status = $("#confirm-status");
    const ok = $("#confirm-ok");
    const ghost = $("#confirm-cancel-btn");
    if (!modal || !pendingConfirm) return;
    $("#confirm-title").textContent = title;
    $("#confirm-copy").textContent = copy;
    if (meta) {
      meta.hidden = false;
      meta.innerHTML = `
        <strong>${escapeHtml(pendingConfirm.name || pendingConfirm.email || "Patient")}</strong>
        <span>${escapeHtml(pendingConfirm.when || "")}</span>
        ${pendingConfirm.email ? `<span>${escapeHtml(pendingConfirm.email)}</span>` : ""}
      `;
    }
    if (status) {
      status.textContent = "";
      status.className = "confirm-status";
    }
    if (ghost) {
      ghost.disabled = false;
      ghost.textContent = ghostLabel || "Cancel";
    }
    if (ok) {
      ok.disabled = false;
      ok.textContent = okLabel;
      ok.classList.remove("confirm-btn-danger", "confirm-btn-teal", "confirm-btn-navy");
      ok.classList.add(okClass || "confirm-btn-danger");
    }
    modal.hidden = false;
  }

  function closeConfirmModal() {
    const modal = $("#confirm-modal");
    if (modal) modal.hidden = true;
    pendingConfirm = null;
    const ok = $("#confirm-ok");
    const ghost = $("#confirm-cancel-btn");
    if (ok) {
      ok.disabled = false;
      ok.textContent = "Cancel appointment";
      ok.classList.remove("confirm-btn-teal", "confirm-btn-navy");
      ok.classList.add("confirm-btn-danger");
    }
    if (ghost) {
      ghost.disabled = false;
      ghost.textContent = "Keep booking";
    }
  }

  async function confirmModalAction() {
    if (!pendingConfirm?.id) return;
    const kind = pendingConfirm.kind;
    const ok = $("#confirm-ok");
    const status = $("#confirm-status");
    const ghost = $("#confirm-cancel-btn");
    if (ok) {
      ok.disabled = true;
      ok.textContent =
        kind === "cancel" ? "Cancelling…" : kind === "treat" ? "Marking treated…" : "Updating…";
    }
    if (ghost) ghost.disabled = true;
    if (status) {
      status.textContent =
        kind === "cancel"
          ? "Cancelling and emailing patient…"
          : kind === "treat"
            ? "Moving visit to Treated history…"
            : "Moving visit back to Confirmed…";
      status.className = "confirm-status";
    }
    try {
      if (kind === "approve") {
        const data = await api(`/admin/api/bookings/${pendingConfirm.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "approved" }),
        });
        closeConfirmModal();
        if (ghost) ghost.disabled = false;
        loadBookings();
        loadStats();
        if (data?.email_sent === false) {
          alert("Appointment approved, but the confirmation email could not be sent.");
        }
        return;
      }
      if (kind === "reject") {
        const data = await api(`/admin/api/bookings/${pendingConfirm.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "rejected" }),
        });
        closeConfirmModal();
        if (ghost) ghost.disabled = false;
        loadBookings();
        loadStats();
        if (data?.email_sent === false) {
          alert("Appointment rejected, but the rejection email could not be sent.");
        }
        return;
      }
      if (kind === "cancel") {
        const data = await api(`/admin/api/bookings/${pendingConfirm.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "cancelled" }),
        });
        closeConfirmModal();
        if (ghost) ghost.disabled = false;
        loadBookings();
        loadStats();
        if (data?.email_sent === false) {
          alert("Appointment cancelled, but the confirmation email could not be sent.");
        }
        return;
      }
      await toggleBookingTreated(pendingConfirm.id, pendingConfirm.currentlyTreated);
      closeConfirmModal();
      if (ghost) ghost.disabled = false;
    } catch (ex) {
      if (status) {
        status.textContent = ex.message || "Could not update appointment.";
        status.className = "confirm-status is-error";
      }
      if (ok) {
        ok.disabled = false;
        ok.textContent =
          kind === "cancel"
            ? "Cancel appointment"
            : kind === "treat"
              ? "Mark as treated"
              : "Mark not treated";
      }
      if (ghost) ghost.disabled = false;
    }
  }

  function fitPhotoCard(img) {
    const viewer = img?.closest(".pm-photo-viewer");
    if (!viewer || !img?.naturalWidth || !img.naturalHeight) return;
    const ratio = img.naturalWidth / img.naturalHeight;
    const maxH = Math.min(window.innerHeight * 0.42, 400);
    const panel = viewer.closest(".pm-photo-panel");
    const fullW = panel?.clientWidth || viewer.clientWidth || img.clientWidth;
    viewer.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
    if (fullW / ratio > maxH) {
      viewer.style.width = `${Math.round(maxH * ratio)}px`;
      viewer.style.maxHeight = `${Math.round(maxH)}px`;
      viewer.style.marginInline = "auto";
    } else {
      viewer.style.width = "100%";
      viewer.style.maxHeight = "";
      viewer.style.marginInline = "";
    }
  }

  function setPhotoLoadingFrame(viewer) {
    if (!viewer) return;
    viewer.style.aspectRatio = "16 / 10";
    viewer.style.width = "100%";
    viewer.style.maxHeight = `${Math.min(window.innerHeight * 0.42, 400)}px`;
    viewer.style.marginInline = "";
  }

  function treatmentRecommendations(report, findings) {
    const treatments = Array.isArray(report?.treatments) ? report.treatments : [];
    const fromFindings = [];
    const details = findings?.concern_details;
    if (Array.isArray(details)) {
      details.forEach((row) => {
        if (!row || typeof row !== "object") return;
        const opts = row.treatment_options;
        if (Array.isArray(opts)) {
          opts.forEach((o) => {
            const t = String(o || "").trim();
            if (t) fromFindings.push(t);
          });
        } else if (typeof opts === "string" && opts.trim()) {
          opts.split(",").forEach((part) => {
            const t = part.trim();
            if (t) fromFindings.push(t);
          });
        }
      });
    }
    const raw = treatments.length ? treatments : fromFindings;
    const seen = new Set();
    const out = [];
    raw.forEach((item) => {
      const t = String(item || "").trim();
      if (!t) return;
      if (/^phase\s*\d+\s*:/i.test(t)) return;
      const key = t.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push(t);
    });
    return out;
  }

  function photoCacheKey(reportId, slot) {
    const id = String(reportId || "").trim();
    const key = String(slot || "front").trim() || "front";
    // Front shares the reports-list thumb cache key for instant modal opens.
    if (key === "front") return id;
    return id ? `${id}:${key}` : key;
  }

  function renderPhotoCarousel() {
    const img = $("#pm-photo-img");
    const skeleton = $("#pm-photo-skeleton");
    const placeholder = $("#pm-photo-placeholder");
    const callout = $("#pm-photo-callout");
    const calloutIdx = $("#pm-photo-callout-index");
    const calloutLabel = $("#pm-photo-callout-label");
    const viewName = $("#pm-view-name");
    const dots = $("#pm-view-dots");
    const prev = $("#pm-photo-prev");
    const next = $("#pm-photo-next");
    const views = photoCarousel.views;
    const i = photoCarousel.index;
    const loadToken = ++photoLoadSeq;

    if (!views.length) {
      if (img) {
        img.hidden = true;
        img.classList.remove("is-loaded");
        img.removeAttribute("src");
        img.onload = null;
        img.onerror = null;
      }
      if (skeleton) skeleton.hidden = true;
      const viewer = $("#pm-body")?.querySelector(".pm-photo-viewer");
      setPhotoLoadingFrame(viewer);
      if (placeholder) placeholder.hidden = false;
      if (callout) callout.hidden = true;
      if (viewName) viewName.textContent = "No smile photo";
      if (dots) dots.innerHTML = "";
      if (prev) prev.disabled = true;
      if (next) next.disabled = true;
      return;
    }

    const view = views[i];
    if (img) {
      img.alt = view.label;
      const showLoaded = () => {
        if (loadToken !== photoLoadSeq) return;
        if (skeleton) skeleton.hidden = true;
        if (placeholder) placeholder.hidden = true;
        img.hidden = false;
        // Next frame so opacity/transform transition can play.
        requestAnimationFrame(() => {
          if (loadToken !== photoLoadSeq) return;
          img.classList.add("is-loaded");
          fitPhotoCard(img);
        });
      };
      const showFailed = () => {
        if (loadToken !== photoLoadSeq) return;
        if (skeleton) skeleton.hidden = true;
        img.hidden = true;
        img.classList.remove("is-loaded");
        if (placeholder) {
          placeholder.hidden = false;
          const p = placeholder.querySelector("p");
          if (p) p.textContent = "Could not load smile photo";
        }
      };
      const showLoading = () => {
        img.classList.remove("is-loaded");
        img.hidden = true;
        if (placeholder) placeholder.hidden = true;
        if (skeleton) skeleton.hidden = false;
        setPhotoLoadingFrame(img.closest(".pm-photo-viewer"));
      };
      const applySrc = (src) => {
        if (loadToken !== photoLoadSeq) return;
        if (!src) {
          showFailed();
          return;
        }
        img.onload = showLoaded;
        img.onerror = showFailed;
        if (img.src === src && img.complete && img.naturalWidth) {
          showLoaded();
          return;
        }
        if (img.src !== src) {
          showLoading();
          img.src = src;
        } else if (img.complete && img.naturalWidth) {
          showLoaded();
        } else {
          showLoading();
        }
      };

      const cacheKey = photoCacheKey(openReportId, view.key);
      const cached = thumbCache.get(cacheKey)?.objectUrl;
      if (cached) {
        applySrc(cached);
      } else {
        showLoading();
        resolveThumbUrl(cacheKey, view.url).then(applySrc);
      }
    }
    if (callout) callout.hidden = false;
    if (calloutIdx) calloutIdx.textContent = String(i + 1);
    if (calloutLabel) calloutLabel.textContent = view.label;
    if (viewName) viewName.textContent = view.label;
    if (dots) {
      dots.innerHTML = views
        .map(
          (_, idx) =>
            `<button type="button" class="pm-dot${idx === i ? " active" : ""}" data-photo-dot="${idx}" aria-label="Show ${escapeHtml(views[idx].label)}"></button>`
        )
        .join("");
    }
    if (prev) prev.disabled = views.length < 2;
    if (next) next.disabled = views.length < 2;
  }

  function setPhotoIndex(nextIndex) {
    const n = photoCarousel.views.length;
    if (!n) return;
    photoCarousel.index = ((nextIndex % n) + n) % n;
    renderPhotoCarousel();
  }

  function renderPatientDetail(data, { animateBars = true } = {}) {
    const body = $("#pm-body");
    const title = $("#pm-title");
    if (!body) return;

    const r = data.report || {};
    const bookings = data.bookings || [];
    const photos = data.photos || {};
    const findings = r.findings && typeof r.findings === "object" ? r.findings : {};
    const categoryScores =
      (r.category_scores && typeof r.category_scores === "object" && r.category_scores) ||
      (findings.scores && typeof findings.scores === "object" && findings.scores) ||
      {};

    const preferredId =
      openReportOrigin?.bookingId ||
      openReportBooking?.id ||
      null;
    const linked = resolveLinkedBooking(bookings, preferredId);
    const name = displayName(r.email, linked?.name);
    const initials = initialsFrom(r.email, linked?.name);
    const score = r.overall_score != null ? Number(r.overall_score) : null;
    const concerns = Array.isArray(r.concerns) ? r.concerns : [];
    const recItems = treatmentRecommendations(r, findings);

    syncPmTreatBtn(linked);
    const treated = linked ? bookingIsTreated(linked) : false;
    const canManage =
      linked && (linked.status === "confirmed" || treated);
    const fromAppointments = openReportOrigin?.source === "appointments";
    const whenLabel = linked ? formatBookingWhen(linked.date, linked.time) : "";
    const statusLabel = treated
      ? "Treated"
      : linked?.status === "cancelled"
        ? "Cancelled"
        : linked?.status === "confirmed"
          ? "Confirmed"
          : linked
            ? prettyLabel(String(linked.status || "Booked"))
            : "";

    const apptActionsHtml = linked
      ? `${
          canManage
            ? `<button type="button" class="pm-header-treat-btn${treated ? " is-treated" : ""}"
                data-pm-toggle-treated="${escapeHtml(linked.id)}"
                data-treated="${treated ? "1" : "0"}"
                data-treat-name="${escapeHtml(linked.name || name || "")}"
                data-treat-email="${escapeHtml(linked.email || r.email || "")}"
                data-treat-when="${escapeHtml(whenLabel)}">
                ${treated ? "Mark not treated" : "Mark as treated"}
              </button>`
            : ""
        }
        <button type="button" class="pm-header-back-btn" data-focus-booking="${escapeHtml(linked.id)}">
          ${fromAppointments ? "Back to appointment" : "Open in Appointments"}
        </button>`
      : "";
    const headerActions = $("#pm-header-appt-actions");
    if (headerActions) {
      if (apptActionsHtml) {
        headerActions.innerHTML = apptActionsHtml;
        headerActions.hidden = false;
      } else {
        headerActions.innerHTML = "";
        headerActions.hidden = true;
      }
    }

    const views = [];
    [
      ["front", "Front smile"],
      ["left", "Left smile"],
      ["right", "Right smile"],
    ].forEach(([key, label]) => {
      if (photos[key]) views.push({ key, label, url: photos[key] });
    });
    photoCarousel = { views, index: Math.min(photoCarousel.index || 0, Math.max(0, views.length - 1)) };
    views.forEach((v) => {
      const key = photoCacheKey(r.id, v.key);
      if (key && v.url && !thumbCache.has(key) && !thumbInflight.has(key)) {
        resolveThumbUrl(key, v.url).catch(() => {});
      }
    });

    const scoreRows = CATEGORY_META.map(({ key, label }) => {
      const value = categoryScores[key];
      if (value == null || !Number.isFinite(Number(value))) return "";
      const n = Math.max(0, Math.min(100, Math.round(Number(value))));
      const band = scoreBand(n);
      return `
        <div class="pm-score-row">
          <div class="pm-score-bar-cell">
            <div class="pm-score-label">${escapeHtml(label)}</div>
            <div class="pm-score-bar-wrap">
              <div class="pm-score-bar-fill ${barClass(n)}" style="width:${n}%"></div>
            </div>
          </div>
          <div class="pm-score-val">${n}/100</div>
          <div class="pm-score-band ${band.cls}">${band.label}</div>
        </div>
      `;
    }).filter(Boolean).join("");

    const circumference = 2 * Math.PI * 52;
    const ringOffset =
      score != null
        ? circumference * (1 - Math.max(0, Math.min(100, score)) / 100)
        : circumference;

    body.innerHTML = `
      <section class="pm-photo-panel">
        <div class="pm-photo-viewer">
          <div class="pm-photo-callout" id="pm-photo-callout" hidden>
            <span class="pm-photo-callout-index" id="pm-photo-callout-index">1</span>
            <span id="pm-photo-callout-label">Front smile</span>
          </div>
          <img class="pm-photo-img" id="pm-photo-img" alt="" hidden />
          <div class="pm-photo-skeleton" id="pm-photo-skeleton" hidden aria-hidden="true"></div>
          <div class="pm-photo-placeholder" id="pm-photo-placeholder">
            <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
            <p>No smile photo stored</p>
          </div>
        </div>
        <div class="pm-photo-bar">
          <button type="button" class="pm-photo-arrow prev" id="pm-photo-prev" aria-label="Previous photo">‹</button>
          <div class="pm-photo-bar-center">
            <div class="pm-view-dots" id="pm-view-dots"></div>
            <div class="pm-view-name" id="pm-view-name">No smile photo</div>
          </div>
          <button type="button" class="pm-photo-arrow next" id="pm-photo-next" aria-label="Next photo">›</button>
        </div>
      </section>

      <div class="pm-col pm-col-side">
      <section class="pm-patient-panel">
        <div class="pm-patient-top">
          <div class="pm-patient-info-block">
            <div class="pm-panel-header">
              <h3>Patient Information</h3>
            </div>
            <div class="pm-patient-id-row">
              <div class="pm-avatar" aria-hidden="true">${escapeHtml(initials)}</div>
              <div>
                <div class="pm-patient-name">${escapeHtml(name)}</div>
                <div class="pm-patient-contact">${escapeHtml(r.email || "—")}</div>
                <div class="pm-patient-contact">${escapeHtml(r.phone || "—")}</div>
              </div>
            </div>
            <div class="pm-patient-demographics">
              <div class="pm-demo-item">
                <span class="pm-demo-label">Gender</span>
                <span class="pm-demo-val">${escapeHtml(r.gender || linked?.gender || "—")}</span>
              </div>
              <div class="pm-demo-item">
                <span class="pm-demo-label">Age</span>
                <span class="pm-demo-val">${(r.age != null ? r.age : linked?.age != null ? linked.age : null) != null ? `${escapeHtml(r.age != null ? r.age : linked.age)} yrs` : "—"}</span>
              </div>
              <div class="pm-demo-item">
                <span class="pm-demo-label">City</span>
                <span class="pm-demo-val">${escapeHtml(r.city || linked?.city || "—")}</span>
              </div>
            </div>
          </div>
            <section class="pm-score-ring-panel">
              <div class="pm-panel-header">
                <span class="pm-panel-star">★</span>
                <h3>Total score</h3>
              </div>
            <div class="pm-score-ring-body">
              <div class="pm-ring-svg-wrap">
                <svg class="pm-ring-svg" viewBox="0 0 120 120" aria-hidden="true">
                  <circle class="pm-ring-track" cx="60" cy="60" r="52"></circle>
                  <circle class="pm-ring-fill" cx="60" cy="60" r="52"
                    stroke-dasharray="${circumference.toFixed(2)}"
                    stroke-dashoffset="${ringOffset.toFixed(2)}"></circle>
                </svg>
                <div class="pm-ring-center-text ${score != null ? scoreBand(score).cls : "watch"}">
                  <div class="pm-ring-score">${score != null ? score : "—"}</div>
                  <div class="pm-ring-max">/ 100</div>
                </div>
              </div>
              <div class="pm-ring-info">
                <h4>${score != null ? scoreBand(score).label : "Pending"}</h4>
                <p>Preliminary AI smile score from the uploaded photo(s).</p>
              </div>
            </div>
          </section>
        </div>
        <div class="pm-appt-block${linked ? "" : " is-empty"}${fromAppointments && linked ? " is-bridged" : ""}">
          ${
            linked
              ? `<div class="pm-patient-block-eyebrow">${
                  fromAppointments ? "Linked appointment" : "Upcoming Appointment"
                }</div>
                <div class="pm-appt-grid">
                  <div>
                    <div class="pm-appt-cell-label">Date</div>
                    <div class="pm-appt-cell-value">${escapeHtml(linked.date || "—")}</div>
                  </div>
                  <div>
                    <div class="pm-appt-cell-label">Time</div>
                    <div class="pm-appt-cell-value">${escapeHtml(formatTime12(linked.time) || "—")}</div>
                  </div>
                  <div>
                    <div class="pm-appt-cell-label">Status</div>
                    <div class="pm-appt-cell-value">${escapeHtml(statusLabel || "—")}</div>
                  </div>
                </div>`
              : `<div class="pm-appt-copy">
                  <div class="pm-patient-block-eyebrow">Upcoming Appointment</div>
                  <p class="pm-cta-text">No appointment linked yet.</p>
                </div>
                <button type="button" class="pm-appt-book-btn" data-book-from-report="${escapeHtml(r.id)}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                  Book appointment
                </button>`
          }
        </div>
      </section>

      <section class="pm-case-panel">
        <div class="pm-panel-header">
          <h3>Case Information</h3>
        </div>
          <div class="pm-case-grid">
            <div class="pm-case-cell">
              <div class="pm-case-cell-label">Submitted</div>
              <div class="pm-case-cell-value">${escapeHtml(formatWhen(r.created_at) || "—")}</div>
            </div>
            <div class="pm-case-cell">
              <div class="pm-case-cell-label">Concerns</div>
              <div class="pm-case-cell-value">${concerns.length}</div>
            </div>
            <div class="pm-case-cell">
              <div class="pm-case-cell-label">Priority</div>
              <div class="pm-case-cell-value">${escapeHtml(String(findings.priority_level || scoreBand(score).label).replace(/_/g, " "))}</div>
            </div>
          </div>
        <div class="pm-history-row">
          <div>
            <div class="pm-history-label">Visible concerns</div>
            <div class="pm-history-date">${
              concerns.length
                ? escapeHtml(concerns.slice(0, 3).map((c) => String(c).replace(/_/g, " ")).join(", "))
                : "None flagged"
            }</div>
          </div>
        </div>
      </section>
      </div>

      <div class="pm-col pm-col-scores">
        <section class="pm-scores-panel">
          <div class="pm-panel-header">
            <h3>Smile scores</h3>
          </div>
          <div class="pm-scores-body">
            ${scoreRows || `<p class="pm-cta-text">No category scores available.</p>`}
          </div>
        </section>
      </div>

      <section class="pm-treatment-panel">
        <div class="pm-panel-header">
          <span class="pm-panel-star">★</span>
          <h3>Recommendations</h3>
        </div>
        <div class="pm-treatment-body">
          ${
            recItems.length
              ? recItems
                  .slice(0, 8)
                  .map(
                    (t) => `
                <div class="pm-treatment-item">
                  <span class="pm-treatment-dot"></span>
                  <span>${escapeHtml(String(t))}</span>
                </div>`
                  )
                  .join("")
              : `<p class="pm-cta-text" style="margin:0">No treatment recommendations listed.</p>`
          }
        </div>
      </section>
    `;

    if (title) title.textContent = name;
    renderPhotoCarousel();

    const chatHist = data.chat_history || r.chat_history || null;
    const chatBtn = $("#pm-view-chat-btn");
    if (chatBtn) {
      if (Array.isArray(chatHist) && chatHist.length > 0) {
        chatBtn.hidden = false;
        chatBtn.onclick = () => openChatHistoryModal(name, chatHist);
      } else {
        chatBtn.hidden = true;
        chatBtn.onclick = null;
      }
    }
    if (animateBars) {
      requestAnimationFrame(() => {
        body.querySelectorAll(".pm-score-bar-fill").forEach((el) => {
          const w = el.style.width;
          el.style.width = "0%";
          requestAnimationFrame(() => {
            el.style.width = w;
          });
        });
      });
    }
  }

  async function openReport(id, opts = {}) {
    const modal = $("#patient-modal");
    const body = $("#pm-body");
    const title = $("#pm-title");
    if (!modal || !body || !id) return;

    openReportId = id;
    const pdfBtn = $("#pm-view-pdf-btn");
    if (pdfBtn) {
      const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
      pdfBtn.href = `/admin/api/reports/${encodeURIComponent(id)}/pdf${tokenParam}`;
      pdfBtn.hidden = false;
    }
    if (opts.fromBooking && opts.fromBooking.id) {
      openReportOrigin = { source: "appointments", bookingId: opts.fromBooking.id };
      syncPmTreatBtn(opts.fromBooking);
      const headerActions = $("#pm-header-appt-actions");
      if (headerActions) {
        const treated = !!opts.fromBooking.treated || bookingIsTreated(opts.fromBooking);
        const whenLabel = formatBookingWhen(opts.fromBooking.date, opts.fromBooking.time);
        headerActions.innerHTML = `
          <button type="button" class="pm-header-treat-btn${treated ? " is-treated" : ""}"
            data-pm-toggle-treated="${escapeHtml(opts.fromBooking.id)}"
            data-treated="${treated ? "1" : "0"}"
            data-treat-name="${escapeHtml(opts.fromBooking.name || "")}"
            data-treat-email="${escapeHtml(opts.fromBooking.email || "")}"
            data-treat-when="${escapeHtml(whenLabel)}">
            ${treated ? "Mark not treated" : "Mark as treated"}
          </button>
          <button type="button" class="pm-header-back-btn" data-focus-booking="${escapeHtml(opts.fromBooking.id)}">
            Back to appointment
          </button>`;
        headerActions.hidden = false;
      }
    } else if (!opts.keepOrigin) {
      openReportOrigin = null;
      syncPmTreatBtn(null);
      const headerActions = $("#pm-header-appt-actions");
      if (headerActions) {
        headerActions.innerHTML = "";
        headerActions.hidden = true;
      }
    }
    const seq = ++openReportSeq;
    modal.hidden = false;

    const cached = getCachedDetail(id);
    if (cached?.data) {
      renderPatientDetail(cached.data, { animateBars: true });
    } else {
      if (title) title.textContent = "Loading…";
      body.innerHTML = `
        <div class="pm-skeleton" role="status" aria-live="polite" aria-label="Loading assessment">
          <div class="pm-skel-photo">
            <div class="pm-skel-block pm-skel-photo-main"></div>
            <div class="pm-skel-block pm-skel-photo-bar"></div>
          </div>
          <div class="pm-skel-side">
            <div class="pm-skel-block pm-skel-patient"></div>
            <div class="pm-skel-block pm-skel-case"></div>
          </div>
          <div class="pm-skel-block pm-skel-scores"></div>
          <div class="pm-skel-block pm-skel-recs"></div>
        </div>
      `;
    }

    try {
      const prevFp = cached?.fingerprint || null;
      const data = await fetchReportDetail(id, { force: true });
      if (seq !== openReportSeq || openReportId !== id) return;
      const nextFp = detailFingerprint(data);
      if (!cached?.data || prevFp !== nextFp) {
        renderPatientDetail(data, { animateBars: !cached?.data });
      } else {
        // Same content; refresh photo URLs in carousel if needed without full remount
        const photos = data.photos || {};
        const views = [];
        [
          ["front", "Front smile"],
          ["left", "Left smile"],
          ["right", "Right smile"],
        ].forEach(([key, label]) => {
          if (photos[key]) views.push({ key, label, url: photos[key] });
        });
        if (views.length) {
          photoCarousel.views = views;
          renderPhotoCarousel();
        }
      }
    } catch (e) {
      if (seq !== openReportSeq || openReportId !== id) return;
      if (!cached?.data) {
        body.innerHTML = `
          <div class="pm-skeleton pm-skeleton-error" role="alert">
            <p>${escapeHtml(e.message || "Could not load assessment.")}</p>
          </div>
        `;
        if (title) title.textContent = "Assessment";
      }
    }
  }

  /* ── Bookings ───────────────────────────────────────── */
  async function loadBookings() {
    const q       = $("#bookings-q").value.trim();
    const [sort, order] = ($("#bookings-sort").value || "date:desc").split(":");
    const status  = $("#bookings-status").value;
    const params  = new URLSearchParams({ sort, order, q, status });
    const queryKey = String(params);
    const list    = $("#bookings-list");
    let lastError = null;

    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const data = await api(`/admin/api/bookings?${params}`);
        const items = Array.isArray(data.items) ? data.items : [];
        if (bookingsPager.queryKey !== queryKey) bookingsPager.page = 1;
        bookingsPager.queryKey = queryKey;
        bookingsPager.items = items;
        renderBookingsPage();
        return;
      } catch (e) {
        lastError = e;
        if (attempt < 3) {
          await new Promise((r) => setTimeout(r, 250 * attempt));
        }
      }
    }
    list.innerHTML = errorState(lastError?.message || "Could not load appointments. Please try again.");
    updateSheetPager("bookings", null);
  }

  /* ── Schedules ──────────────────────────────────────── */
  let schedulesCache = [];

  function schedulePatchPayload(s, overrides = {}) {
    return {
      label: String(s.label || "Schedule").trim(),
      start_date: String(s.start_date || "").slice(0, 10),
      end_date: String(s.end_date || "").slice(0, 10),
      days_of_week: Array.isArray(s.days_of_week) ? s.days_of_week.map(Number) : [1, 2, 3, 4, 5, 6],
      open_time: String(s.open_time || "09:00").slice(0, 5),
      close_time: String(s.close_time || "20:00").slice(0, 5),
      slot_minutes: Number(s.slot_minutes) || 30,
      active: s.active !== false,
      ...overrides,
    };
  }

  function renderSchedules() {
    const list = $("#schedules-list");
    if (!list) return;
    if (!schedulesCache.length) {
      list.innerHTML = emptyState("No schedules yet. Add one on the left.");
      return;
    }
    list.innerHTML = schedulesCache.map((s) => {
      const openDays = new Set(Array.isArray(s.days_of_week) ? s.days_of_week.map(Number) : []);
      const isActive = !!s.active;
      const open = formatTime12(s.open_time);
      const close = formatTime12(s.close_time);
      const step = `${s.slot_minutes || 30} min`;
      const weekOrder = [
        { dow: 1, label: "M" },
        { dow: 2, label: "T" },
        { dow: 3, label: "W" },
        { dow: 4, label: "T" },
        { dow: 5, label: "F" },
        { dow: 6, label: "S" },
        { dow: 0, label: "S" },
      ];
      return `
        <article class="schedule-card${isActive ? " is-active" : " is-inactive"}" role="listitem" data-schedule="${escapeHtml(s.id)}">
          <div class="schedule-card-top">
            <h4 class="schedule-label">${escapeHtml(s.label || "Schedule")}</h4>
            <div class="schedule-active-seg" role="group" aria-label="Schedule status">
              <button type="button" class="schedule-active-btn${isActive ? " is-on" : ""}"
                data-set-active="${escapeHtml(s.id)}" data-active="true" ${isActive ? "aria-pressed=\"true\"" : "aria-pressed=\"false\""}>
                Active
              </button>
              <button type="button" class="schedule-active-btn${!isActive ? " is-on" : ""}"
                data-set-active="${escapeHtml(s.id)}" data-active="false" ${!isActive ? "aria-pressed=\"true\"" : "aria-pressed=\"false\""}>
                Inactive
              </button>
            </div>
          </div>
          <div class="schedule-stat-row">
            <div class="schedule-stat">
              <span class="schedule-stat-label">Dates</span>
              <strong>${escapeHtml(formatIsoDate(s.start_date))} → ${escapeHtml(formatIsoDate(s.end_date))}</strong>
            </div>
            <div class="schedule-stat">
              <span class="schedule-stat-label">Hours</span>
              <strong>${escapeHtml(open)} – ${escapeHtml(close)}</strong>
            </div>
            <div class="schedule-stat">
              <span class="schedule-stat-label">Slots</span>
              <strong>${escapeHtml(step)}</strong>
            </div>
          </div>
          <div class="schedule-card-foot">
            <div class="schedule-days" aria-label="Open days">
              ${weekOrder.map(({ dow, label }) => {
                const on = openDays.has(dow);
                return `<span class="schedule-day${on ? " is-on" : ""}" title="${["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][dow]}">${label}</span>`;
              }).join("")}
            </div>
            <button type="button" class="schedule-delete-btn" data-del-schedule="${escapeHtml(s.id)}" aria-label="Delete schedule">
              Delete
            </button>
          </div>
        </article>
      `;
    }).join("");
  }

  async function loadSchedules() {
    const list = $("#schedules-list");
    try {
      const data = await api("/admin/api/schedules");
      schedulesCache = Array.isArray(data.items) ? data.items : [];
      renderSchedules();
    } catch (e) {
      schedulesCache = [];
      if (list) list.innerHTML = errorState(e.message);
    }
  }

  let walkinViewYear = null;
  let walkinViewMonth = null; // 0-11
  let walkinAllSlots = [];
  let walkinFreeSlots = [];
  let walkinBookedSlots = [];
  let walkinMonthMeta = {};
  let walkinMonthMetaKey = "";

  const WALKIN_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  function isoDateLocal(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function parseIsoDateLocal(iso) {
    const parts = String(iso || "").split("-").map(Number);
    if (parts.length < 3 || parts.some((n) => !Number.isFinite(n))) return null;
    return new Date(parts[0], parts[1] - 1, parts[2]);
  }

  function startOfLocalDay(d = new Date()) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function ensureWalkinViewMonth() {
    const today = startOfLocalDay();
    if (walkinViewYear == null || walkinViewMonth == null) {
      walkinViewYear = today.getFullYear();
      walkinViewMonth = today.getMonth();
    }
    return { year: walkinViewYear, month: walkinViewMonth };
  }

  function populateWalkinMonthYearSelects() {
    const monthSel = $("#w-month");
    const yearSel = $("#w-year");
    if (!monthSel || !yearSel) return;
    const { year, month } = ensureWalkinViewMonth();
    const today = startOfLocalDay();
    const minYear = today.getFullYear();
    const maxYear = minYear + 2;

    if (!monthSel.options.length) {
      monthSel.innerHTML = WALKIN_MONTH_NAMES.map(
        (name, i) => `<option value="${i}">${name}</option>`
      ).join("");
    }
    if (!yearSel.options.length || Number(yearSel.options[0]?.value) !== minYear) {
      const years = [];
      for (let y = minYear; y <= maxYear; y += 1) years.push(y);
      yearSel.innerHTML = years.map((y) => `<option value="${y}">${y}</option>`).join("");
    }

    monthSel.value = String(month);
    yearSel.value = String(year);

    // Disable past months in the current year
    Array.from(monthSel.options).forEach((opt) => {
      const m = Number(opt.value);
      opt.disabled = year === minYear && m < today.getMonth();
    });
  }

  function setWalkinViewMonth(year, month, { keepSelected = true } = {}) {
    const today = startOfLocalDay();
    let y = Number(year);
    let m = Number(month);
    if (!Number.isFinite(y) || !Number.isFinite(m)) return;
    if (m < 0) {
      m = 11;
      y -= 1;
    } else if (m > 11) {
      m = 0;
      y += 1;
    }
    const min = new Date(today.getFullYear(), today.getMonth(), 1);
    const view = new Date(y, m, 1);
    if (view < min) {
      y = today.getFullYear();
      m = today.getMonth();
    }
    walkinViewYear = y;
    walkinViewMonth = m;

    const dateInput = $("#w-date");
    if (!keepSelected && dateInput) {
      const firstSelectable = y === today.getFullYear() && m === today.getMonth()
        ? today
        : new Date(y, m, 1);
      dateInput.value = isoDateLocal(firstSelectable);
    } else if (dateInput) {
      const selected = parseIsoDateLocal(dateInput.value);
      if (!selected || selected < today) {
        dateInput.value = isoDateLocal(today);
      } else if (selected.getFullYear() !== y || selected.getMonth() !== m) {
        // Keep selection if still valid; otherwise pick first day of viewed month (or today)
        const pick = y === today.getFullYear() && m === today.getMonth()
          ? today
          : new Date(y, m, 1);
        dateInput.value = isoDateLocal(pick);
      }
    }

    populateWalkinMonthYearSelects();
    renderWalkinCalendar();
  }

  async function loadWalkinMonthMeta(year, month) {
    const key = `${year}-${month + 1}`;
    if (walkinMonthMetaKey === key && Object.keys(walkinMonthMeta).length) {
      return walkinMonthMeta;
    }
    try {
      const res = await fetch(
        `/api/availability/month?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month + 1)}`
      );
      const data = await res.json();
      if (!res.ok) {
        walkinMonthMeta = {};
        walkinMonthMetaKey = key;
        return walkinMonthMeta;
      }
      walkinMonthMeta = data.days && typeof data.days === "object" ? data.days : {};
      walkinMonthMetaKey = key;
    } catch {
      walkinMonthMeta = {};
      walkinMonthMetaKey = key;
    }
    return walkinMonthMeta;
  }

  function renderWalkinCalendar() {
    const grid = $("#w-cal-grid");
    const dateInput = $("#w-date");
    if (!grid || !dateInput) return;

    const { year, month } = ensureWalkinViewMonth();
    const today = startOfLocalDay();
    populateWalkinMonthYearSelects();

    let selected = dateInput.value;
    const selectedDate = parseIsoDateLocal(selected);
    if (!selectedDate || selectedDate < today) {
      selected = isoDateLocal(today);
      dateInput.value = selected;
    }

    const first = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const startPad = (first.getDay() + 6) % 7; // Monday-first
    const cells = [];

    for (let i = 0; i < startPad; i += 1) {
      const d = new Date(year, month, -startPad + i + 1);
      cells.push(`
        <button type="button" class="book-cal-day is-outside" disabled tabindex="-1" aria-hidden="true">
          ${d.getDate()}
        </button>`);
    }

    for (let day = 1; day <= daysInMonth; day += 1) {
      const d = new Date(year, month, day);
      const key = isoDateLocal(d);
      const past = d < today;
      const isToday = key === isoDateLocal(today);
      const isActive = key === selected;
      const meta = walkinMonthMeta[key];
      const closed = !!meta?.closed;
      const full = !!meta?.full;
      const disabled = past || closed;
      cells.push(`
        <button type="button"
          class="book-cal-day${isActive ? " is-active" : ""}${isToday ? " is-today" : ""}${disabled ? " is-disabled" : ""}${full && !disabled ? " is-full" : ""}"
          role="option"
          data-day="${key}"
          ${disabled ? "disabled" : ""}
          aria-selected="${isActive}"
          aria-label="${escapeHtml(d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }))}">
          ${day}
        </button>`);
    }

    const remainder = cells.length % 7;
    if (remainder) {
      for (let i = 1; i <= 7 - remainder; i += 1) {
        cells.push(`
          <button type="button" class="book-cal-day is-outside" disabled tabindex="-1" aria-hidden="true">
            ${i}
          </button>`);
      }
    }

    grid.innerHTML = cells.join("");
    refreshWalkinHint();
    updateWalkinSelectedSummary();

    loadWalkinMonthMeta(year, month).then((meta) => {
      if (walkinViewYear !== year || walkinViewMonth !== month) return;
      if (!meta || !Object.keys(meta).length) return;
      // Re-paint availability classes without resetting selection
      grid.querySelectorAll(".book-cal-day[data-day]").forEach((btn) => {
        const key = btn.dataset.day;
        const info = meta[key];
        if (!info) return;
        const d = parseIsoDateLocal(key);
        const past = d && d < today;
        const closed = !!info.closed;
        const full = !!info.full;
        const disabled = past || closed;
        btn.classList.toggle("is-full", full && !disabled);
        btn.classList.toggle("is-disabled", disabled);
        btn.disabled = disabled;
      });
    });
  }

  function updateWalkinSelectedSummary() {
    const text = $("#w-selected-text");
    if (!text) return;
    const day = $("#w-date")?.value || "";
    const time = $("#w-time")?.value || "";
    const d = parseIsoDateLocal(day);
    if (!d || !time) {
      text.textContent = "Pick a date and time";
      return;
    }
    const dateLabel = d.toLocaleDateString(undefined, {
      weekday: "long",
      month: "long",
      day: "numeric",
    });
    const idx = walkinAllSlots.indexOf(time);
    let end = null;
    if (idx >= 0 && idx < walkinAllSlots.length - 1) {
      end = walkinAllSlots[idx + 1];
    } else {
      const parts = String(time).split(":").map(Number);
      if (parts.length >= 2 && parts.every((n) => Number.isFinite(n))) {
        const total = parts[0] * 60 + parts[1] + 30;
        end = `${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
      }
    }
    const range = end ? `${formatTime12(time)} – ${formatTime12(end)}` : formatTime12(time);
    text.textContent = `${dateLabel}, ${range}`;
  }

  function syncWalkinTimeUi({ disabled = false, label = "Select a date first", allSlots = [], freeSlots = [], bookedSlots = [], selected = "" } = {}) {
    const time = $("#w-time");
    const grid = $("#w-slot-grid");
    if (!time || !grid) return;

    walkinAllSlots = Array.isArray(allSlots) ? allSlots.slice() : [];
    walkinFreeSlots = Array.isArray(freeSlots) ? freeSlots.slice() : [];
    walkinBookedSlots = Array.isArray(bookedSlots) ? bookedSlots.slice() : [];

    if (disabled || (!walkinAllSlots.length && !walkinFreeSlots.length)) {
      time.value = "";
      time.disabled = true;
      grid.innerHTML = `<div class="book-slot-empty">${escapeHtml(label)}</div>`;
      updateWalkinSelectedSummary();
      return;
    }

    const chosen = walkinFreeSlots.includes(selected) ? selected : (walkinFreeSlots[0] || "");
    time.disabled = !chosen;
    time.value = chosen;
    renderWalkinSlotGrid();
    updateWalkinSelectedSummary();
  }

  function renderWalkinSlotGrid() {
    const grid = $("#w-slot-grid");
    const time = $("#w-time");
    if (!grid || !time) return;
    const chosen = time.value;

    const slotsToRender = walkinAllSlots.length
      ? walkinAllSlots
      : [...new Set([...walkinFreeSlots, ...walkinBookedSlots])].sort();

    grid.innerHTML = slotsToRender
      .map((slot) => {
        const isFree = walkinFreeSlots.includes(slot);
        const isBooked = walkinBookedSlots.includes(slot) || !isFree;
        const isActive = slot === chosen && isFree;
        const label = formatTime12(slot);
        const displayLabel = isBooked ? `${label} (Taken)` : label;
        return `
          <button type="button" class="book-slot${isActive ? " is-active" : ""}${isBooked ? " is-taken is-disabled" : ""}"
            role="option" data-slot="${escapeHtml(slot)}" ${isBooked ? "disabled" : ""} aria-selected="${isActive}"
            title="${isBooked ? "This slot is already booked" : "Available slot"}">
            ${escapeHtml(displayLabel)}
          </button>`;
      })
      .join("");
  }

  async function refreshWalkinHint() {
    const day = $("#w-date")?.value;
    const hint = $("#w-slots-hint");
    const time = $("#w-time");
    if (!hint || !time) return;
    if (!day) {
      hint.textContent = "";
      syncWalkinTimeUi({ disabled: true, label: "Select a date first" });
      return;
    }
    try {
      const res = await fetch(`/api/availability?date=${encodeURIComponent(day)}`);
      const data = await res.json();
      if (!res.ok) {
        hint.textContent = data.detail || "Could not load slots.";
        syncWalkinTimeUi({ disabled: true, label: "No open times" });
        return;
      }
      let freeSlots = Array.isArray(data.slots) ? data.slots : [];
      let bookedSlots = Array.isArray(data.booked) ? data.booked : [];
      let allSlots = Array.isArray(data.all_slots) ? data.all_slots : [];
      const todayIso = isoDateLocal(startOfLocalDay());
      if (day === todayIso) {
        const now = new Date();
        const currentHhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
        freeSlots = freeSlots.filter((s) => s > currentHhmm);
        allSlots = allSlots.filter((s) => s > currentHhmm);
      }
      if (!freeSlots.length && !allSlots.length) {
        hint.textContent = data.closed
          ? "Clinic closed on this date."
          : "No free slots on this date.";
        syncWalkinTimeUi({ disabled: true, label: "No open times" });
        return;
      }
      const previous = time.value;
      syncWalkinTimeUi({
        disabled: false,
        label: "Select a time",
        allSlots,
        freeSlots,
        bookedSlots,
        selected: previous,
      });
      if (data.open_time && data.close_time) {
        hint.textContent = `Clinic hours: ${formatTime12(data.open_time)} – ${formatTime12(data.close_time)} (${freeSlots.length} available, ${bookedSlots.length} taken)`;
      } else {
        hint.textContent = `${freeSlots.length} slots available (${bookedSlots.length} taken)`;
      }
    } catch {
      hint.textContent = "Could not load slots.";
      syncWalkinTimeUi({ disabled: true, label: "Could not load times" });
    }
  }

  function initWalkinBookingUi() {
    const dateInput = $("#w-date");
    const today = startOfLocalDay();
    let selected = parseIsoDateLocal(dateInput?.value);
    if (!selected || selected < today) {
      if (dateInput) dateInput.value = isoDateLocal(today);
      selected = today;
    }
    walkinViewYear = selected.getFullYear();
    walkinViewMonth = selected.getMonth();
    walkinMonthMeta = {};
    walkinMonthMetaKey = "";
    populateWalkinMonthYearSelects();
    renderWalkinCalendar();
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

  $("#topbar-bell")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const panel = $("#topbar-notify-panel");
    if (panel && !panel.hidden) closeNotifyPanel();
    else {
      closeDashFilterMenu();
      openNotifyPanel();
    }
  });

  $("#topbar-notify-clear")?.addEventListener("click", (e) => {
    e.stopPropagation();
    markAllNotifsRead();
  });

  $("#topbar-notify-list")?.addEventListener("click", (e) => {
    const item = e.target.closest("[data-notif-id]");
    if (!item) return;
    markNotifRead(item.dataset.notifId);
    closeNotifyPanel();
    const action = item.dataset.notifAction || "appointments";
    showTab(action);
  });

  $("#dash-filter-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const wrap = $("#dash-filter-wrap");
    const menu = $("#dash-filter-menu");
    const btn = $("#dash-filter-btn");
    const open = !!wrap?.classList.contains("is-open");
    closeNotifyPanel();
    if (open) {
      closeDashFilterMenu();
      return;
    }
    wrap?.classList.add("is-open");
    if (menu) menu.hidden = false;
    btn?.setAttribute("aria-expanded", "true");
  });

  $("#dash-filter-menu")?.addEventListener("click", (e) => {
    const opt = e.target.closest(".clinic-filter-option");
    if (!opt) return;
    setDashFilter(opt.dataset.value);
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#topbar-notify-wrap")) closeNotifyPanel();
    if (!e.target.closest("#dash-filter-wrap")) closeDashFilterMenu();
  });

  $("#dash-patient-list")?.addEventListener("click", (e) => {
    const reportBtn = e.target.closest("[data-open-report]");
    if (reportBtn) {
      e.stopPropagation();
      const id = reportBtn.dataset.openReport;
      if (!id) return;
      showTab("reports");
      openReport(id);
      return;
    }
    const row = e.target.closest("[data-dash-patient]");
    if (!row) return;
    dashState.selectedId = row.dataset.dashPatient;
    renderDashPatientList();
  });

  $("#dash-brief-body")?.addEventListener("click", (e) => {
    const reportBtn = e.target.closest("[data-open-report]");
    if (reportBtn) {
      const id = reportBtn.dataset.openReport;
      if (!id) return;
      showTab("reports");
      openReport(id);
      return;
    }
    const row = e.target.closest("[data-dash-patient]");
    if (!row) return;
    dashState.selectedId = row.dataset.dashPatient;
    renderDashPatientList();
  });

  // Reports filters
  ["reports-q"].forEach((id) => {
    $(`#${id}`)?.addEventListener("input", () => {
      clearTimeout(reportsDebounce);
      reportsDebounce = setTimeout(loadReports, 200);
    });
    $(`#${id}`)?.addEventListener("change", loadReports);
  });

  function closeReportsSortMenu() {
    const menu = $("#reports-sort-menu");
    const btn = $("#reports-sort-btn");
    if (menu) menu.hidden = true;
    if (btn) btn.setAttribute("aria-expanded", "false");
    $("#reports-sort-wrap")?.classList.remove("is-open");
  }

  function setReportsSortValue(value, label) {
    const select = $("#reports-sort");
    const btnLabel = $("#reports-sort-btn-label");
    if (select) select.value = value;
    if (btnLabel) btnLabel.textContent = label;
    $$(".reports-sort-option").forEach((opt) => {
      const on = opt.dataset.value === value;
      opt.classList.toggle("is-active", on);
      opt.setAttribute("aria-selected", String(on));
    });
  }

  $("#reports-sort-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeBookingsSortMenu();
    const menu = $("#reports-sort-menu");
    const btn = $("#reports-sort-btn");
    const open = !!menu?.hidden;
    if (menu) menu.hidden = !open;
    if (btn) btn.setAttribute("aria-expanded", String(open));
    $("#reports-sort-wrap")?.classList.toggle("is-open", open);
  });

  $("#reports-sort-menu")?.addEventListener("click", (e) => {
    const opt = e.target.closest(".reports-sort-option");
    if (!opt) return;
    setReportsSortValue(opt.dataset.value, opt.textContent.trim());
    closeReportsSortMenu();
    loadReports();
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#reports-sort-wrap")) closeReportsSortMenu();
    if (!e.target.closest("#bookings-sort-wrap")) closeBookingsSortMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeReportsSortMenu();
      closeBookingsSortMenu();
    }
  });

  document.querySelector(".reports-toolbar")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".reports-chip");
    if (!chip || chip.closest(".bookings-toolbar")) return;
    const band = chip.dataset.reportsBand;
    const date = chip.dataset.reportsDate;
    if (band != null) {
      chip.parentElement?.querySelectorAll(".reports-chip").forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      const input = $("#reports-band");
      if (input) input.value = band;
      reportsPager.page = 1;
      loadReports();
      return;
    }
    if (date != null) {
      chip.parentElement?.querySelectorAll(".reports-chip").forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      const input = $("#reports-date");
      if (input) input.value = date;
      reportsPager.page = 1;
      loadReports();
    }
  });

  $("#reports-pager")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pager='reports']");
    if (!btn || btn.disabled) return;
    let nextPage = reportsPager.page;
    if (btn.dataset.page) nextPage = Number(btn.dataset.page);
    else if (btn.dataset.dir === "prev") nextPage = reportsPager.page - 1;
    else if (btn.dataset.dir === "next") nextPage = reportsPager.page + 1;
    const info = pageSlice(reportsPager.items, nextPage);
    if (info.page === reportsPager.page) return;
    reportsPager.page = info.page;
    renderReportsPage();
    $("#reports-list")?.scrollTo?.({ top: 0 });
  });

  $("#bookings-pager")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pager='bookings']");
    if (!btn || btn.disabled) return;
    let nextPage = bookingsPager.page;
    if (btn.dataset.page) nextPage = Number(btn.dataset.page);
    else if (btn.dataset.dir === "prev") nextPage = bookingsPager.page - 1;
    else if (btn.dataset.dir === "next") nextPage = bookingsPager.page + 1;
    const info = pageSlice(bookingsPager.items, nextPage);
    if (info.page === bookingsPager.page) return;
    bookingsPager.page = info.page;
    renderBookingsPage();
    $("#bookings-list")?.scrollTo?.({ top: 0 });
  });

  // Bookings filters
  $("#bookings-q")?.addEventListener("input", () => {
    clearTimeout(bookingsDebounce);
    bookingsDebounce = setTimeout(loadBookings, 200);
  });

  function closeBookingsSortMenu() {
    const menu = $("#bookings-sort-menu");
    const btn = $("#bookings-sort-btn");
    if (menu) menu.hidden = true;
    if (btn) btn.setAttribute("aria-expanded", "false");
    $("#bookings-sort-wrap")?.classList.remove("is-open");
  }

  function setBookingsSortValue(value, label) {
    const select = $("#bookings-sort");
    const btnLabel = $("#bookings-sort-btn-label");
    if (select) select.value = value;
    if (btnLabel) btnLabel.textContent = label;
    $$("#bookings-sort-menu .reports-sort-option").forEach((opt) => {
      const on = opt.dataset.value === value;
      opt.classList.toggle("is-active", on);
      opt.setAttribute("aria-selected", String(on));
    });
  }

  $("#bookings-sort-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeReportsSortMenu();
    const menu = $("#bookings-sort-menu");
    const btn = $("#bookings-sort-btn");
    const open = !!menu?.hidden;
    if (menu) menu.hidden = !open;
    if (btn) btn.setAttribute("aria-expanded", String(open));
    $("#bookings-sort-wrap")?.classList.toggle("is-open", open);
  });

  $("#bookings-sort-menu")?.addEventListener("click", (e) => {
    const opt = e.target.closest(".reports-sort-option");
    if (!opt) return;
    setBookingsSortValue(opt.dataset.value, opt.textContent.trim());
    closeBookingsSortMenu();
    loadBookings();
  });

  document.querySelector(".bookings-toolbar")?.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-bookings-status]");
    if (!chip) return;
    chip.parentElement?.querySelectorAll(".reports-chip").forEach((c) => c.classList.remove("is-active"));
    chip.classList.add("is-active");
    const input = $("#bookings-status");
    if (input) input.value = chip.dataset.bookingsStatus;
    bookingsPager.page = 1;
    loadBookings();
  });

  $("#w-month")?.addEventListener("change", () => {
    setWalkinViewMonth(Number($("#w-year")?.value), Number($("#w-month")?.value), {
      keepSelected: false,
    });
  });
  $("#w-year")?.addEventListener("change", () => {
    setWalkinViewMonth(Number($("#w-year")?.value), Number($("#w-month")?.value), {
      keepSelected: false,
    });
  });

  $("#w-cal-grid")?.addEventListener("click", (e) => {
    const dayBtn = e.target.closest(".book-cal-day[data-day]");
    if (!dayBtn || dayBtn.disabled) return;
    const day = dayBtn.dataset.day;
    if (!day) return;
    const dateInput = $("#w-date");
    if (dateInput) dateInput.value = day;
    renderWalkinCalendar();
  });

  $("#w-slot-grid")?.addEventListener("click", (e) => {
    const slotBtn = e.target.closest(".book-slot");
    if (!slotBtn) return;
    const slot = slotBtn.dataset.slot;
    if (!slot) return;
    const time = $("#w-time");
    if (time) time.value = slot;
    renderWalkinSlotGrid();
    updateWalkinSelectedSummary();
  });

  // Open report modal + hover prefetch (Instagram-style)
  $("#reports-list")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-report]");
    if (btn) openReport(btn.dataset.report);
  });
  $("#reports-list")?.addEventListener("pointerenter", (e) => {
    const btn = e.target.closest?.("[data-report]");
    if (!btn?.dataset.report) return;
    const id = btn.dataset.report;
    if (!getCachedDetail(id) && !detailInflight.has(id)) {
      fetchReportDetail(id).catch(() => {});
    }
  }, true);

  $("#pm-header-appt-actions")?.addEventListener("click", (e) => {
    const treatBtn = e.target.closest("[data-pm-toggle-treated]");
    if (treatBtn) {
      openTreatConfirm({
        id: treatBtn.dataset.pmToggleTreated,
        name: treatBtn.dataset.treatName || "",
        email: treatBtn.dataset.treatEmail || "",
        when: treatBtn.dataset.treatWhen || "",
        currentlyTreated: treatBtn.dataset.treated === "1",
      });
      return;
    }
    const focusBtn = e.target.closest("[data-focus-booking]");
    if (focusBtn) {
      focusBookingInAppointments(focusBtn.dataset.focusBooking);
    }
  });

  // Patient modal photo carousel + appointment bridge + close
  $("#pm-body")?.addEventListener("click", (e) => {
    if (e.target.closest("#pm-photo-prev")) {
      setPhotoIndex(photoCarousel.index - 1);
      return;
    }
    if (e.target.closest("#pm-photo-next")) {
      setPhotoIndex(photoCarousel.index + 1);
      return;
    }
    const bookBtn = e.target.closest("[data-book-from-report]");
    if (bookBtn) {
      startBookingFromReport(bookBtn.dataset.bookFromReport);
      return;
    }
    const treatBtn = e.target.closest("[data-pm-toggle-treated]");
    if (treatBtn) {
      openTreatConfirm({
        id: treatBtn.dataset.pmToggleTreated,
        name: treatBtn.dataset.treatName || "",
        email: treatBtn.dataset.treatEmail || "",
        when: treatBtn.dataset.treatWhen || "",
        currentlyTreated: treatBtn.dataset.treated === "1",
      });
      return;
    }
    const focusBtn = e.target.closest("[data-focus-booking]");
    if (focusBtn) {
      focusBookingInAppointments(focusBtn.dataset.focusBooking);
      return;
    }
    const dot = e.target.closest("[data-photo-dot]");
    if (dot) setPhotoIndex(Number(dot.dataset.photoDot));
  });

  $$("[data-close-patient-modal]").forEach((el) =>
    el.addEventListener("click", closePatientModal)
  );

  $$("[data-close-chat-modal]").forEach((el) =>
    el.addEventListener("click", closeChatHistoryModal)
  );

  // Cancel / rebook booking
  $("#bookings-list")?.addEventListener("click", (e) => {
    const reportBtn = e.target.closest("[data-view-report]");
    if (reportBtn) {
      const id = reportBtn.dataset.viewReport;
      if (id) {
        openReport(id, {
          fromBooking: {
            id: reportBtn.dataset.bookingId || "",
            name: reportBtn.dataset.treatName || "",
            email: reportBtn.dataset.treatEmail || "",
            status: reportBtn.dataset.bookingStatus || "confirmed",
            treated: reportBtn.dataset.treated === "1",
            date: reportBtn.dataset.bookingDate || "",
            time: reportBtn.dataset.bookingTime || "",
          },
        });
      }
      return;
    }
    const treatBtn = e.target.closest("[data-toggle-treated]");
    if (treatBtn) {
      openTreatConfirm({
        id: treatBtn.dataset.toggleTreated,
        name: treatBtn.dataset.treatName || "",
        email: treatBtn.dataset.treatEmail || "",
        when: treatBtn.dataset.treatWhen || "",
        currentlyTreated: treatBtn.dataset.treated === "1",
      });
      return;
    }
    const approveBtn = e.target.closest("[data-approve]");
    if (approveBtn) {
      pendingConfirm = {
        kind: "approve",
        id: approveBtn.dataset.approve,
        name: approveBtn.dataset.approveName || "",
        email: approveBtn.dataset.approveEmail || "",
        when: approveBtn.dataset.approveWhen || "",
      };
      showConfirmModal({
        title: "Approve appointment?",
        copy: "This will approve the appointment and send the confirmation email to the patient.",
        okLabel: "Approve & Send Email",
        ghostLabel: "Keep pending",
        okClass: "confirm-btn-teal",
      });
      return;
    }
    const rejectBtn = e.target.closest("[data-reject]");
    if (rejectBtn) {
      pendingConfirm = {
        kind: "reject",
        id: rejectBtn.dataset.reject,
        name: rejectBtn.dataset.rejectName || "",
        email: rejectBtn.dataset.rejectEmail || "",
        when: rejectBtn.dataset.rejectWhen || "",
      };
      showConfirmModal({
        title: "Reject appointment?",
        copy: "This will reject the appointment request. No confirmation email will be sent.",
        okLabel: "Reject appointment",
        ghostLabel: "Keep pending",
        okClass: "confirm-btn-danger",
      });
      return;
    }
    const cancelBtn = e.target.closest("[data-cancel]");
    if (cancelBtn) {
      openCancelConfirm(cancelBtn);
      return;
    }
    const rebookBtn = e.target.closest("[data-book-again]");
    if (rebookBtn) {
      startBookingAgain(rebookBtn);
    }
  });

  $("#confirm-ok")?.addEventListener("click", () => {
    confirmModalAction();
  });

  $$("[data-close-confirm]").forEach((el) =>
    el.addEventListener("click", () => {
      if ($("#confirm-ok")?.disabled) return;
      closeConfirmModal();
    })
  );

  // Schedule list actions (delete + active/inactive)
  $("#schedules-list")?.addEventListener("click", async (e) => {
    const activeBtn = e.target.closest("[data-set-active]");
    if (activeBtn) {
      const id = activeBtn.dataset.setActive;
      const next = activeBtn.dataset.active === "true";
      const current = schedulesCache.find((row) => String(row.id) === String(id));
      if (!current) return;
      if (!!current.active === next) return; // already in the desired state

      // ── Optimistic UI: apply locally before API responds ──
      if (next) {
        // Activating this schedule → mark every other as inactive in local cache.
        schedulesCache.forEach((s) => { s.active = String(s.id) === String(id); });
      } else {
        current.active = false;
      }
      activeBtn.disabled = true;
      renderSchedules(); // instant re-render from updated cache – no API call

      try {
        // Use the dedicated /active sub-route which enforces single-active atomically.
        await api(`/admin/api/schedules/${id}/active`, {
          method: "PATCH",
          body: JSON.stringify({ active: next }),
        });
        // Re-fetch authoritative state (other schedules may have been deactivated server-side).
        await loadSchedules();
      } catch (ex) {
        // Roll back optimistic update on error.
        schedulesCache.forEach((s) => { s.active = String(s.id) === String(id) ? !next : s.active; });
        renderSchedules();
        alert(ex.message);
      }
      return;
    }

    const btn = e.target.closest("[data-del-schedule]");
    if (!btn) return;
    if (!confirm("Delete this schedule? This cannot be undone.")) return;
    try {
      await api(`/admin/api/schedules/${btn.dataset.delSchedule}`, { method: "DELETE" });
      loadSchedules();
    } catch (ex) { alert(ex.message); }
  });

  // ESC closes patient / confirm modal
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("#confirm-modal") && !$("#confirm-modal").hidden) {
      if (!$("#confirm-ok")?.disabled) closeConfirmModal();
      return;
    }
    if ($("#topbar-notify-panel") && !$("#topbar-notify-panel").hidden) {
      closeNotifyPanel();
      return;
    }
    if ($("#dash-filter-wrap")?.classList.contains("is-open")) {
      closeDashFilterMenu();
      return;
    }
    if ($("#patient-chat-modal") && !$("#patient-chat-modal").hidden) {
      closeChatHistoryModal();
      return;
    }
    if ($("#patient-modal") && !$("#patient-modal").hidden) {
      closePatientModal();
    }
  });

  function initAdminCityAutocomplete() {
    const input = $("#w-city");
    const list = $("#w-city-dropdown-list");
    const toggle = $("#w-city-toggle-btn");
    const otherWrap = $("#w-other-city-wrap");
    const otherInput = $("#w-other-city");
    if (!input || !list) return;

    let activeIndex = -1;

    function renderList(cities) {
      if (!cities.length) {
        list.innerHTML = `<li class="autocomplete-empty">No cities found</li>`;
      } else {
        list.innerHTML = cities
          .map(
            (c, i) =>
              `<li class="autocomplete-item${i === activeIndex ? " is-highlighted" : ""}" role="option" data-value="${escapeHtml(c)}">${escapeHtml(c)}</li>`
          )
          .join("");
      }
      list.hidden = false;
    }

    function checkOtherField() {
      const val = input.value.trim();
      if (val === "Other") {
        if (otherWrap) otherWrap.hidden = false;
      } else {
        if (otherWrap) otherWrap.hidden = true;
        if (otherInput) otherInput.value = "";
      }
    }

    function filterCities() {
      const query = input.value.trim().toLowerCase();
      const matches = query
        ? PAKISTANI_CITIES.filter((c) => c.toLowerCase().includes(query))
        : PAKISTANI_CITIES;
      renderList(matches);
      checkOtherField();
    }

    input.addEventListener("focus", () => filterCities());
    input.addEventListener("input", () => {
      activeIndex = -1;
      filterCities();
    });

    input.addEventListener("keydown", (e) => {
      const items = list.querySelectorAll(".autocomplete-item");
      if (!items.length || list.hidden) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, items.length - 1);
        filterCities();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        filterCities();
      } else if (e.key === "Enter" && activeIndex >= 0) {
        e.preventDefault();
        const selected = items[activeIndex]?.dataset?.value;
        if (selected) {
          input.value = selected;
          list.hidden = true;
          checkOtherField();
        }
      } else if (e.key === "Escape") {
        list.hidden = true;
      }
    });

    list.addEventListener("click", (e) => {
      const item = e.target.closest(".autocomplete-item");
      if (!item) return;
      input.value = item.dataset.value;
      list.hidden = true;
      checkOtherField();
    });

    if (toggle) {
      toggle.addEventListener("click", (e) => {
        e.preventDefault();
        if (list.hidden) filterCities();
        else list.hidden = true;
      });
    }

    document.addEventListener("click", (e) => {
      if (!e.target.closest("#w-city-autocomplete-wrap")) {
        list.hidden = true;
      }
    });
  }

  // Initialize city autocomplete
  initAdminCityAutocomplete();

  // Walk-in form submit
  $("#walkin-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("#walkin-status");
    const name = $("#w-name")?.value.trim() || "";
    const email = $("#w-email")?.value.trim() || "";
    const phone = $("#w-phone")?.value.trim() || "";
    const gender = $("#w-gender")?.value || "";
    const ageVal = $("#w-age")?.value.trim() || "";
    const rawCity = $("#w-city")?.value.trim() || "";
    const finalCity = rawCity === "Other" ? ($("#w-other-city")?.value.trim() || "") : rawCity;

    if (!name || !email || !phone || !gender || !ageVal || !finalCity) {
      status.textContent = "Please fill out all required fields: Name, Phone, Email, Gender, Age, and City.";
      status.className = "status is-error";
      return;
    }

    const timeVal = $("#w-time")?.value || "";
    if (!$("#w-date")?.value || !timeVal) {
      status.textContent = "Choose a date and an available time from clinic hours.";
      status.className = "status is-error";
      return;
    }

    status.textContent = "Saving…";
    status.className   = "status";
    try {
      const created = await api("/admin/api/bookings", {
        method: "POST",
        body:   JSON.stringify({
          name:   $("#w-name").value.trim(),
          email:  $("#w-email").value.trim(),
          phone:  $("#w-phone").value.trim(),
          gender: $("#w-gender")?.value || null,
          age:    ageVal ? Number(ageVal) : null,
          city:   finalCity || null,
          date:   $("#w-date").value,
          time:   timeVal,
          note:   $("#w-note").value.trim() || null,
          assessment_id: $("#w-assessment-id")?.value.trim() || null,
          source: "admin",
        }),
      });
      status.textContent = created?.email_sent
        ? "Booking confirmed. Confirmation email sent to the patient."
        : "Booking confirmed. Email could not be sent — check email settings.";
      status.className   = created?.email_sent === false ? "status is-error" : "status is-ok";
      
      const confirmBtn = $("#w-confirm-btn");
      if (confirmBtn) confirmBtn.hidden = true;
      const newBtn = $("#w-new-booking-btn");
      if (newBtn) {
        newBtn.hidden = false;
        newBtn.focus();
      }

      if ($("#w-assessment-id")) $("#w-assessment-id").value = "";
      loadBookings();
      loadStats();
    } catch (ex) {
      status.textContent = ex.message;
      status.className   = "status is-error";
      const confirmBtn = $("#w-confirm-btn");
      if (confirmBtn) confirmBtn.hidden = false;
      const newBtn = $("#w-new-booking-btn");
      if (newBtn) newBtn.hidden = false;
    }
  });

  $("#w-new-booking-btn")?.addEventListener("click", () => {
    resetWalkinBookingForm();
    $("#w-name")?.focus();
  });

  // Clear error message when editing walk-in form fields
  $("#walkin-form")?.addEventListener("input", () => {
    const status = $("#walkin-status");
    if (status?.classList.contains("is-error")) {
      status.textContent = "";
      status.className = "status";
      const newBtn = $("#w-new-booking-btn");
      if (newBtn) newBtn.hidden = true;
      const confirmBtn = $("#w-confirm-btn");
      if (confirmBtn) confirmBtn.hidden = false;
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
  window.addEventListener("focus", () => {
    if (!token) return;
    refreshAll();
    if (openReportId && !$("#patient-modal")?.hidden) refreshOpenReport(true);
  });

  /* ── Bootstrap ──────────────────────────────────────── */
  if (token) {
    setLoggedIn(true);
    const preferredTab = safeGetTab();
    if (preferredTab) showTab(preferredTab);
  }
})();
