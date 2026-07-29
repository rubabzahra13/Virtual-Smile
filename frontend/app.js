(() => {
  const state = {
    email: "",
    phone: "",
    frontFile: null,
    leftFile: null,
    rightFile: null,
    reportText: "",
    findings: null,
    overallScore: null,
    categoryScores: null,
    simulationAllowed: true,
    rawOutput: "",
    provider: "gemini",
    model: "gemini-3.5-flash-lite",
    qualityModel: "gemini-3.5-flash-lite",
    chatUsed: 0,
    chatHistory: [],
    photosLocked: false,
  };

  const FREE_CHAT_LIMIT = 5;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  function closeChatPanel() {
    const float = $("#chat-float");
    const panel = $("#chat-panel");
    const fab = $("#chat-fab");
    if (panel) panel.hidden = true;
    if (float) float.classList.remove("is-open");
    if (fab) fab.setAttribute("aria-expanded", "false");
  }

  function openChatPanel() {
    const float = $("#chat-float");
    const panel = $("#chat-panel");
    const fab = $("#chat-fab");
    if (panel) panel.hidden = false;
    if (float) float.classList.add("is-open");
    if (fab) fab.setAttribute("aria-expanded", "true");
    const input = $("#chat-input");
    if (input && !input.disabled) input.focus();
  }

  function setChatFloatVisible(show) {
    const float = $("#chat-float");
    if (!float) return;
    float.hidden = !show;
    if (!show) closeChatPanel();
  }

  function showStep(step) {
    $$(".flow-tab").forEach((tab) => {
      const n = Number(tab.dataset.step);
      tab.classList.toggle("is-active", n === step);
      tab.classList.toggle("is-done", n < step);
      if (n <= step) tab.disabled = false;
    });
    $$(".panel").forEach((panel) => {
      const active = Number(panel.dataset.panel) === step;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    setChatFloatVisible(step === 3);
  }

  function setPhotosLocked(locked) {
    state.photosLocked = !!locked;
    [
      ["#front-image", "frontFile"],
      ["#left-image", "leftFile"],
      ["#right-image", "rightFile"],
    ].forEach(([inputSel]) => {
      const input = $(inputSel);
      if (!input) return;
      const tile = input.closest(".upload-tile");
      const removeBtn = tile?.querySelector(".upload-remove");
      input.disabled = state.photosLocked;
      if (tile) tile.classList.toggle("is-locked", state.photosLocked);
      if (removeBtn) {
        if (state.photosLocked) {
          removeBtn.hidden = true;
          removeBtn.disabled = true;
        } else if (tile?.classList.contains("has-file")) {
          removeBtn.hidden = false;
          removeBtn.disabled = false;
        }
      }
    });
  }

  function clearUploadTile(tile, input, key) {
    if (state.photosLocked) return;
    if (input._previewUrl) {
      URL.revokeObjectURL(input._previewUrl);
      input._previewUrl = null;
    }
    input.value = "";
    state[key] = null;
    tile.classList.remove("has-file");
    const preview = tile.querySelector(".upload-preview");
    const readyTag = tile.querySelector(".upload-ready-tag");
    const removeBtn = tile.querySelector(".upload-remove");
    if (preview) {
      preview.hidden = true;
      preview.removeAttribute("src");
    }
    if (readyTag) readyTag.hidden = true;
    if (removeBtn) removeBtn.hidden = true;
    $("#run-analysis").disabled = !state.frontFile;
  }

  function setUploadTileFile(tile, input, key, file) {
    if (state.photosLocked) return;
    if (input._previewUrl) URL.revokeObjectURL(input._previewUrl);
    state[key] = file;
    const preview = tile.querySelector(".upload-preview");
    const readyTag = tile.querySelector(".upload-ready-tag");
    const removeBtn = tile.querySelector(".upload-remove");
    if (file) {
      tile.classList.add("has-file");
      input._previewUrl = URL.createObjectURL(file);
      preview.hidden = false;
      preview.src = input._previewUrl;
      if (readyTag) readyTag.hidden = false;
      if (removeBtn) removeBtn.hidden = false;
    } else {
      clearUploadTile(tile, input, key);
    }
    $("#run-analysis").disabled = !state.frontFile;
  }

  function wireUploads() {
    [
      ["#front-image", "frontFile"],
      ["#left-image", "leftFile"],
      ["#right-image", "rightFile"],
    ].forEach(([inputSel, key]) => {
      const input = $(inputSel);
      const tile = input.closest(".upload-tile");
      const removeBtn = tile.querySelector(".upload-remove");

      input.addEventListener("change", () => {
        if (state.photosLocked) {
          input.value = "";
          return;
        }
        const file = input.files?.[0] || null;
        setUploadTileFile(tile, input, key, file);
      });

      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (state.photosLocked) return;
          clearUploadTile(tile, input, key);
        });
        // Keep label from opening the file picker when Remove is tapped.
        removeBtn.addEventListener("mousedown", (e) => e.preventDefault());
        removeBtn.addEventListener("touchstart", (e) => e.stopPropagation(), { passive: true });
      }
    });
  }

  function setStatus(el, message, isError = false) {
    el.textContent = message || "";
    el.classList.toggle("is-error", !!isError);
  }

  const CATEGORY_ORDER = [
    "alignment",
    "gum_health",
    "color",
    "restorations",
    "missing_teeth",
  ];

  function normalizeCategoryScores(scores) {
    if (!scores || typeof scores !== "object") return null;
    const out = {};
    for (const [rawKey, rawValue] of Object.entries(scores)) {
      const key = String(rawKey).toLowerCase().replace(/\s+/g, "_");
      if (!CATEGORY_ORDER.includes(key)) continue;
      const num = Number(rawValue);
      if (Number.isFinite(num)) out[key] = num;
    }
    return Object.keys(out).length ? out : null;
  }

  function parseCategoryScores(reportText) {
    if (!reportText) return null;
    const scores = {};
    const patterns = [
      ["alignment", /\bAlignment\b[:\s]+(\d+)/i],
      ["gum_health", /\bGum\s+Health\b[:\s]+(\d+)/i],
      ["color", /\bColor\b[:\s]+(\d+)/i],
      ["restorations", /\bRestorations\b[:\s]+(\d+)/i],
      ["missing_teeth", /\bMissing\s+Teeth\b[:\s]+(\d+)/i],
    ];
    for (const [key, re] of patterns) {
      const m = reportText.match(re);
      if (m) scores[key] = Number(m[1]);
    }
    return Object.keys(scores).length ? scores : null;
  }

  function extractCategoryScores(data, reportText) {
    return (
      normalizeCategoryScores(data?.category_scores) ||
      normalizeCategoryScores(data?.findings?.scores) ||
      parseCategoryScores(reportText)
    );
  }

  function formatConcernLabel(label) {
    return String(label || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (m) => m.toUpperCase());
  }

  function scoreBand(score) {
    if (score >= 90) return { tone: "good", label: "Good" };
    if (score >= 75) return { tone: "watch", label: "Watch" };
    return { tone: "attention", label: "Attention" };
  }

  function renderCategories(scores) {
    const host = $("#category-scores");
    const block = $("#scores-block");
    if (!host) return;
    host.innerHTML = "";

    const normalized = normalizeCategoryScores(scores);
    if (!normalized) {
      if (block) block.hidden = true;
      return;
    }
    if (block) block.hidden = false;

    const labels = {
      alignment: "Alignment",
      gum_health: "Gum health",
      color: "Tooth colour",
      restorations: "Restorations",
      missing_teeth: "Missing teeth",
    };
    const icons = {
      alignment: "",
      gum_health: "",
      color: "",
      restorations: "",
      missing_teeth: "",
    };

    CATEGORY_ORDER.forEach((key) => {
      if (normalized[key] == null) return;
      const value = normalized[key];
      const band = scoreBand(Number(value || 0));
      const row = document.createElement("div");
      row.className = `cat-row cat-row-${band.tone}`;
      row.innerHTML = `
        <div class="cat-name">${labels[key] || key}</div>
        <div class="cat-bar"><i style="width:0%"></i></div>
        <span class="cat-score">${value}<span class="cat-score-max">/100</span></span>
        <span class="cat-band">${band.label}</span>
      `;
      host.appendChild(row);
      requestAnimationFrame(() => {
        const bar = row.querySelector("i");
        if (bar) bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
      });
    });
  }

  function renderPatientFindings(findings) {
    const tags = $("#concern-tags");
    const cards = $("#concern-cards");
    const roadmap = $("#roadmap-list");
    const summary = $("#report-summary-text");
    const notes = $("#patient-notes");
    if (!tags || !cards || !roadmap || !summary || !notes) return;

    tags.innerHTML = "";
    cards.innerHTML = "";
    roadmap.innerHTML = "";
    notes.hidden = true;
    notes.textContent = "";

    const visible = findings?.visible_concerns || [];
    const details = findings?.concern_details || [];
    const detailByConcern = new Map(
      details
        .filter((d) => d && d.concern)
        .map((d) => [d.concern, d])
    );

    if (!visible.length) {
      summary.textContent =
        "Good news: no obvious visible concerns were detected in your uploaded photo(s).";
      const ok = document.createElement("span");
      ok.className = "concern-tag concern-tag-good";
      ok.textContent = "No visible concerns";
      tags.appendChild(ok);
    } else {
      summary.textContent =
        "Visible areas to discuss with your dentist.";
      visible.forEach((c) => {
        const chip = document.createElement("span");
        chip.className = "concern-tag concern-tag-alert";
        chip.textContent = formatConcernLabel(c);
        tags.appendChild(chip);

        const detail = detailByConcern.get(c);
        const card = document.createElement("article");
        card.className = "concern-card";
        const options = Array.isArray(detail?.treatment_options)
          ? detail.treatment_options
          : [];
        card.innerHTML = `
          <h5 class="concern-card-title">${formatConcernLabel(c)}</h5>
          <div class="concern-columns">
            <div class="concern-detail">
              <span class="concern-detail-label">Meaning</span>
              <p>${detail?.likely_cause || "This was visible in the photo and may need a professional check."}</p>
            </div>
            <div class="concern-detail">
              <span class="concern-detail-label">Treatment</span>
              <p>${options.length ? options.join(", ") : "A dental consultation to confirm and plan treatment."}</p>
            </div>
          </div>
        `;
        cards.appendChild(card);
      });
    }

    const roadmapItems = Array.isArray(findings?.treatment_roadmap)
      ? findings.treatment_roadmap
      : [];
    if (roadmapItems.length) {
      roadmapItems.forEach((item, idx) => {
        const step = document.createElement("div");
        step.className = "roadmap-item";
        step.innerHTML = `<span class="roadmap-step">${idx + 1}</span><p>${item}</p>`;
        roadmap.appendChild(step);
      });
    } else {
      roadmap.innerHTML =
        '<div class="roadmap-item"><span class="roadmap-step">1</span><p>Book a routine dentist visit to confirm this AI screening.</p></div>';
    }

    if (findings?.notes) {
      notes.hidden = false;
      notes.textContent = findings.notes;
    }
  }

  function setScoreRing(score) {
    const circle = document.querySelector(".score-ring-value");
    const label = $("#overall-score");
    const circumference = 2 * Math.PI * 52;
    const safe = typeof score === "number" ? score : 0;
    if (label) label.textContent = typeof score === "number" ? score : "-";
    if (circle) {
      circle.style.strokeDasharray = `${circumference}`;
      circle.style.strokeDashoffset = `${circumference * (1 - safe / 100)}`;
    }
  }

  function addChatBubble(text, who) {
    const log = $("#chat-log");
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${who}`;
    bubble.textContent = text;
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
  }

  function chatRemaining() {
    return Math.max(0, FREE_CHAT_LIMIT - state.chatUsed);
  }

  function updateChatLimitUI() {
    const note = $("#chat-limit-note");
    const quota = $("#chat-quota");
    const input = $("#chat-input");
    const submit = $("#chat-submit") || $("#chat-form button[type='submit']");
    const remaining = chatRemaining();

    if (!input) return;

    if (quota) {
      quota.textContent = remaining <= 0 ? "0 left" : `${remaining} left`;
      quota.classList.toggle("is-empty", remaining <= 0);
    }

    if (remaining <= 0) {
      if (note) {
        note.hidden = false;
        note.textContent =
          "You’ve used all 5 free questions. Book a consultation for more personalised advice.";
        note.classList.add("is-locked");
      }
      input.disabled = true;
      input.placeholder = "Free questions used up";
      input.required = false;
      if (submit) submit.disabled = true;
      return;
    }

    if (note) {
      note.hidden = true;
      note.classList.remove("is-locked");
      note.textContent = "";
    }
    input.disabled = false;
    input.required = true;
    input.placeholder = "Ask a question…";
    if (submit) submit.disabled = false;
  }

  function resetChatLimit() {
    state.chatUsed = 0;
    state.chatHistory = [];
    updateChatLimitUI();
  }

  $("#details-form").addEventListener("submit", (e) => {
    e.preventDefault();
    updateContinueEnabled(true);
    const btn = $("#continue-to-photos");
    if (btn?.disabled) return;
    state.email = $("#user-email").value.trim();
    state.phone = formatPakistaniPhone($("#user-phone").value);
    showStep(2);
  });

  function setFieldFeedback(id, message, ok) {
    const el = $(id);
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("is-error", !!message && !ok);
    el.classList.toggle("is-ok", !!ok && !message);
  }

  /** Normalize PK mobile to national 10-digit form starting with 3. */
  function normalizePakistaniMobile(raw) {
    let digits = String(raw || "").replace(/\D/g, "");
    if (digits.startsWith("92") && digits.length >= 12) {
      digits = digits.slice(2);
    }
    if (digits.startsWith("0") && digits.length === 11) {
      digits = digits.slice(1);
    }
    return digits;
  }

  function isValidPakistaniMobile(raw) {
    const digits = normalizePakistaniMobile(raw);
    return /^3\d{9}$/.test(digits);
  }

  function formatPakistaniPhone(raw) {
    const digits = normalizePakistaniMobile(raw);
    return `+92${digits}`;
  }

  function updateContinueEnabled(showErrors = false) {
    const btn = $("#continue-to-photos");
    const emailInput = $("#user-email");
    const phoneInput = $("#user-phone");
    const consentInput = $("#user-consent");
    if (!btn || !emailInput || !phoneInput || !consentInput) return;

    const email = emailInput.value.trim();
    const phone = phoneInput.value.trim();
    const consent = consentInput.checked;
    const phoneDigits = normalizePakistaniMobile(phone);

    let emailMsg = "";
    let phoneMsg = "";
    let consentMsg = "";
    let emailOk = false;
    let phoneOk = false;

    if (!email) {
      emailMsg = showErrors || emailInput.dataset.touched === "1" ? "Email is required." : "";
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      emailMsg = "Enter a valid email, e.g. you@email.com.";
      emailInput.dataset.touched = "1";
    } else {
      emailOk = true;
    }

    if (!phone) {
      phoneMsg = showErrors || phoneInput.dataset.touched === "1" ? "Mobile number is required." : "";
    } else if (!isValidPakistaniMobile(phone)) {
      if (phoneDigits.length > 0 && !phoneDigits.startsWith("3")) {
        phoneMsg = "Must start with 3.";
      } else if (phoneDigits.length > 0 && phoneDigits.length < 10) {
        phoneMsg = `Enter ${10 - phoneDigits.length} more digit${10 - phoneDigits.length === 1 ? "" : "s"} (10 total).`;
      } else if (phoneDigits.length > 10) {
        phoneMsg = "Use 10 digits after +92, e.g. 3XX XXXXXXX.";
      } else {
        phoneMsg = "Enter a valid number, e.g. 300 1234567.";
      }
      phoneInput.dataset.touched = "1";
    } else {
      phoneOk = true;
    }

    if (!consent) {
      consentMsg =
        showErrors || consentInput.dataset.touched === "1"
          ? "Please confirm you understand this is not a diagnosis."
          : "";
    }

    setFieldFeedback("#email-feedback", emailMsg, emailOk);
    setFieldFeedback("#phone-feedback", phoneMsg, phoneOk);
    setFieldFeedback("#consent-feedback", consentMsg, consent);

    emailInput.classList.toggle("is-invalid", !!emailMsg);
    phoneInput.classList.toggle("is-invalid", !!phoneMsg);
    emailInput.closest(".field")?.classList.toggle("is-invalid", !!emailMsg);
    phoneInput.closest(".field")?.classList.toggle("is-invalid", !!phoneMsg);
    consentInput.closest(".consent")?.classList.toggle("is-invalid", !!consentMsg);

    btn.disabled = !(emailOk && phoneOk && consent);
  }

  ["#user-email", "#user-phone"].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.addEventListener("input", () => updateContinueEnabled(false));
    el.addEventListener("blur", () => {
      el.dataset.touched = "1";
      updateContinueEnabled(false);
    });
  });

  const phoneInputEl = $("#user-phone");
  if (phoneInputEl) {
    phoneInputEl.addEventListener("input", () => {
      // Keep only digits in the national-number field.
      const cleaned = phoneInputEl.value.replace(/[^\d\s]/g, "");
      if (cleaned !== phoneInputEl.value) phoneInputEl.value = cleaned;
    });
  }

  const consentEl = $("#user-consent");
  if (consentEl) {
    consentEl.addEventListener("change", () => {
      consentEl.dataset.touched = "1";
      updateContinueEnabled(false);
    });
  }

  updateContinueEnabled(false);

  $("#back-to-details").addEventListener("click", () => showStep(1));

  $("#run-analysis").addEventListener("click", async () => {
    if (!state.frontFile) return;
    const status = $("#analyze-status");
    const btn = $("#run-analysis");
    btn.disabled = true;
    setPhotosLocked(true);
    setStatus(status, "Analysing your smile… this usually takes a few seconds.");

    const formData = new FormData();
    formData.append("front_image", state.frontFile);
    if (state.leftFile) formData.append("left_image", state.leftFile);
    if (state.rightFile) formData.append("right_image", state.rightFile);
    formData.append("provider", state.provider);
    formData.append("model", state.model);
    formData.append("quality_model", state.qualityModel);
    formData.append("two_pass", "true");
    formData.append("email", state.email);
    formData.append("phone", state.phone);

    try {
      const res = await fetch("/analyze", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        const detail = data.detail;
        const msg =
          typeof detail === "string"
            ? detail
            : detail?.message || JSON.stringify(detail);
        throw new Error(msg);
      }

      state.reportText = data.report_text || "";
      state.findings = data.findings || null;
      state.overallScore = data.overall_score;
      state.rawOutput = data.raw_model_output || "";
      state.simulationAllowed = data.simulation_allowed !== false;
      state.categoryScores = extractCategoryScores(data, state.reportText);

      const summaryEl = $("#results-summary");
      if (summaryEl) {
        summaryEl.textContent = data.parsed_ok
          ? "Here’s your preliminary visual assessment and Smile Score."
          : "We generated a report, but some scoring fields could not be parsed cleanly.";
      }
      setScoreRing(state.overallScore);
      renderCategories(state.categoryScores);
      renderPatientFindings(state.findings);

      const before = $("#sim-before");
      const simBlock = $("#sim-block");
      const simSkipNote = $("#sim-skip-note");
      if (state.simulationAllowed) {
        if (simBlock) simBlock.hidden = false;
        if (simSkipNote) simSkipNote.hidden = true;
        before.src = URL.createObjectURL(state.frontFile);
        $("#sim-after").hidden = true;
        $("#generate-sim").hidden = false;
        setStatus($("#sim-status"), "");
      } else {
        if (simBlock) simBlock.hidden = true;
        if (simSkipNote) simSkipNote.hidden = false;
      }

      $("#chat-log").innerHTML = "";
      resetChatLimit();
      addChatBubble(
        "Hi, I’ve reviewed your assessment. Ask me anything about your Smile Score or next steps.",
        "bot"
      );

      showStep(3);
      setStatus(status, "");
    } catch (err) {
      setPhotosLocked(false);
      setStatus(status, err.message || "Analysis failed.", true);
    } finally {
      btn.disabled = !state.frontFile || state.photosLocked;
    }
  });

  $("#generate-sim").addEventListener("click", async () => {
    if (!state.frontFile) return;
    const status = $("#sim-status");
    const btn = $("#generate-sim");
    btn.disabled = true;
    setStatus(status, "Creating an illustrative treatment preview…");

    const formData = new FormData();
    formData.append("front_image", state.frontFile);
    formData.append("report_text", state.reportText || "");
    if (state.findings) {
      formData.append("findings_json", JSON.stringify(state.findings));
    }

    try {
      const res = await fetch("/simulate", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Simulation failed.");

      const after = $("#sim-after");
      after.src = data.image_data_url;
      after.hidden = false;
      btn.hidden = true;
      setStatus(
        status,
        data.disclaimer
          || "Illustrative simulation only - report treatments edited onto your uploaded photo."
      );
    } catch (err) {
      setStatus(status, err.message || "Could not generate preview.", true);
      btn.disabled = false;
    }
  });

  $("#chat-fab")?.addEventListener("click", () => openChatPanel());
  $("#chat-close")?.addEventListener("click", () => closeChatPanel());

  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#chat-input");
    const submit = $("#chat-submit") || $("#chat-form button[type='submit']");
    const question = input.value.trim();
    if (!question) return;

    if (chatRemaining() <= 0) {
      updateChatLimitUI();
      return;
    }

    state.chatUsed += 1;
    updateChatLimitUI();
    input.value = "";
    addChatBubble(question, "user");
    if (submit) submit.disabled = true;

    const priorHistory = state.chatHistory.slice();

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          report_text: state.reportText,
          overall_score: state.overallScore,
          email: state.email,
          history: priorHistory,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Chat failed.");
      const answer = data.answer || "";
      addChatBubble(answer, "bot");
      state.chatHistory.push({ role: "user", content: question });
      state.chatHistory.push({ role: "assistant", content: answer });
    } catch (err) {
      addChatBubble(
        "Sorry - I couldn’t answer that just now. Please try again, or book a consultation for clinical advice.",
        "bot"
      );
    } finally {
      updateChatLimitUI();
      if (chatRemaining() <= 0) {
        addChatBubble(
          "You’ve reached your 5 free questions for this assessment. If you’d like a deeper discussion, our team would be happy to help at a consultation.",
          "bot"
        );
      }
    }
  });

  function showAssessInHero() {
    const shell = document.querySelector(".hero-shell");
    const landing = $("#hero-landing");
    const assess = $("#assess");
    const orb = document.querySelector(".hero-orb-cta");
    if (shell) shell.classList.add("is-assessing");
    if (landing) landing.hidden = true;
    if (assess) assess.hidden = false;
    if (orb) orb.hidden = true;
    pauseHeroSlider();
    document.body.classList.add("flow-open");
    document.body.classList.remove("hero-only");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showAboutFlow() {
    const flow = $("#app-flow");
    const footer = $("#site-footer");
    if (flow) flow.hidden = false;
    if (footer) footer.hidden = false;
    document.body.classList.add("flow-open");
    const el = $("#how");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showAppFlow(target = "assess") {
    if (target === "about") {
      showAboutFlow();
      return;
    }
    showAssessInHero();
  }

  function showHeroOnly() {
    const shell = document.querySelector(".hero-shell");
    const landing = $("#hero-landing");
    const assess = $("#assess");
    const orb = document.querySelector(".hero-orb-cta");
    const flow = $("#app-flow");
    const footer = $("#site-footer");
    if (shell) shell.classList.remove("is-assessing");
    if (landing) landing.hidden = false;
    if (assess) assess.hidden = true;
    if (orb) orb.hidden = false;
    if (flow) flow.hidden = true;
    if (footer) footer.hidden = true;
    setChatFloatVisible(false);
    resumeHeroSlider();
    document.body.classList.remove("flow-open");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  let heroSlideIndex = 0;
  let heroSlideTimer = null;
  let heroSliderPaused = false;
  let heroRealCount = 0;
  let heroTrackJumping = false;

  function heroLogicalIndex(index = heroSlideIndex) {
    if (!heroRealCount) return 0;
    return index % heroRealCount;
  }

  function updateHeroDots(logical) {
    $$(".hero-dot").forEach((dot, i) => {
      const active = i === logical;
      dot.classList.toggle("is-active", active);
      dot.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function updateHeroSlideState(index) {
    $$(".hero-slide").forEach((slide, i) => {
      if (slide.classList.contains("is-clone")) {
        slide.classList.remove("is-active");
        slide.setAttribute("aria-hidden", "true");
        return;
      }
      const active = i === heroLogicalIndex(index);
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", active ? "false" : "true");
    });
    updateHeroDots(heroLogicalIndex(index));
  }

  function setHeroSlide(index, { user = false, instant = false } = {}) {
    const track = $("#hero-track");
    const slides = $$(".hero-slide");
    if (!track || !slides.length || !heroRealCount) return;

    heroSlideIndex = Math.max(0, index);
    updateHeroSlideState(heroSlideIndex);

    if (instant) {
      heroTrackJumping = true;
      track.classList.add("is-jumping");
      track.style.transform = `translateX(-${heroSlideIndex * 100}%)`;
      // Force reflow so the next move animates again.
      void track.offsetWidth;
      track.classList.remove("is-jumping");
      heroTrackJumping = false;
    } else {
      track.style.transform = `translateX(-${heroSlideIndex * 100}%)`;
    }

    if (user) {
      heroSliderPaused = false;
      restartHeroSlider();
    }
  }

  function goHeroNext({ user = false } = {}) {
    if (heroSlideIndex >= heroRealCount) {
      setHeroSlide(heroSlideIndex % heroRealCount, { instant: true });
    }
    setHeroSlide(heroSlideIndex + 1, { user });
  }

  function goHeroToLogical(logical, { user = false } = {}) {
    const target = ((logical % heroRealCount) + heroRealCount) % heroRealCount;
    const current = heroLogicalIndex();

    if (target === current && heroSlideIndex < heroRealCount) {
      if (user) {
        heroSliderPaused = false;
        restartHeroSlider();
      }
      return;
    }

    // Always move forward: e.g. from 1 → 0 goes via clone (index 2).
    if (target > current) {
      setHeroSlide(target, { user });
    } else {
      setHeroSlide(heroRealCount + target, { user });
    }
  }

  function pauseHeroSlider() {
    heroSliderPaused = true;
    if (heroSlideTimer) {
      clearInterval(heroSlideTimer);
      heroSlideTimer = null;
    }
  }

  function resumeHeroSlider() {
    heroSliderPaused = false;
    restartHeroSlider();
  }

  function restartHeroSlider() {
    if (heroSlideTimer) clearInterval(heroSlideTimer);
    if (heroSliderPaused) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    heroSlideTimer = setInterval(() => {
      goHeroNext();
    }, 5200);
  }

  function initHeroSlider() {
    const slider = $("#hero-landing");
    const track = $("#hero-track");
    if (!slider || !track) return;

    const realSlides = $$(".hero-slide:not(.is-clone)");
    heroRealCount = realSlides.length;
    if (heroRealCount < 2) return;

    // Clone first slide at the end so 1 → 2 → 1 animates forward.
    if (!track.querySelector(".hero-slide.is-clone")) {
      const clone = realSlides[0].cloneNode(true);
      clone.classList.add("is-clone");
      clone.classList.remove("is-active");
      clone.removeAttribute("data-slide");
      clone.setAttribute("aria-hidden", "true");
      clone.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
      track.appendChild(clone);
    }

    track.addEventListener("transitionend", (e) => {
      if (e.target !== track || e.propertyName !== "transform") return;
      if (heroTrackJumping) return;
      if (heroSlideIndex >= heroRealCount) {
        setHeroSlide(heroSlideIndex % heroRealCount, { instant: true });
      }
    });

    $$(".hero-dot").forEach((dot) => {
      dot.addEventListener("click", () => {
        goHeroToLogical(Number(dot.dataset.slideTo) || 0, { user: true });
      });
    });

    let startX = 0;
    let deltaX = 0;
    let swiping = false;

    slider.addEventListener(
      "touchstart",
      (e) => {
        if (!e.touches[0]) return;
        startX = e.touches[0].clientX;
        deltaX = 0;
        swiping = true;
        pauseHeroSlider();
      },
      { passive: true }
    );

    slider.addEventListener(
      "touchmove",
      (e) => {
        if (!swiping || !e.touches[0]) return;
        deltaX = e.touches[0].clientX - startX;
      },
      { passive: true }
    );

    slider.addEventListener("touchend", () => {
      if (!swiping) return;
      swiping = false;
      if (Math.abs(deltaX) > 42) {
        // Always advance forward (1 → 2 → 1), never reverse-wrap.
        goHeroNext({ user: true });
      } else {
        resumeHeroSlider();
      }
    });

    setHeroSlide(0, { instant: true });
    resumeHeroSlider();
  }

  function toLocalISODate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function formatBookTimeLabel(value) {
    if (!value) return "";
    const [hRaw, mRaw] = value.split(":");
    const h = Number(hRaw);
    const m = Number(mRaw || 0);
    if (Number.isNaN(h)) return value;
    const suffix = h >= 12 ? "PM" : "AM";
    const hour12 = h % 12 || 12;
    return `${hour12}:${String(m).padStart(2, "0")} ${suffix}`;
  }

  function syncBookDateChips(iso) {
    $$("#book-date-strip .book-date-chip").forEach((chip) => {
      const on = chip.dataset.date === iso;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  function syncBookTimeChips(value) {
    $$("#book-time-grid .book-time-slot").forEach((slot) => {
      const on = slot.dataset.time === value;
      slot.classList.toggle("is-active", on);
      slot.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  function buildBookDates(selectedIso) {
    const strip = $("#book-date-strip");
    const dateInput = $("#book-date");
    if (!strip || !dateInput) return;

    const days = [];
    const cursor = new Date();
    cursor.setHours(12, 0, 0, 0);
    cursor.setDate(cursor.getDate() + 1);

    while (days.length < 14) {
      if (cursor.getDay() !== 0) {
        days.push(new Date(cursor));
      }
      cursor.setDate(cursor.getDate() + 1);
    }

    const minIso = toLocalISODate(days[0]);
    dateInput.min = minIso;

    const preferred =
      selectedIso && selectedIso >= minIso ? selectedIso : minIso;

    strip.innerHTML = days
      .map((d) => {
        const iso = toLocalISODate(d);
        const selected = iso === preferred;
        return `
          <button
            type="button"
            class="book-date-chip${selected ? " is-active" : ""}"
            role="option"
            data-date="${iso}"
            aria-selected="${selected ? "true" : "false"}"
          >
            <span class="book-date-chip-dow">${d.toLocaleDateString(undefined, { weekday: "short" })}</span>
            <span class="book-date-chip-day">${d.getDate()}</span>
            <span class="book-date-chip-mon">${d.toLocaleDateString(undefined, { month: "short" })}</span>
          </button>
        `;
      })
      .join("");

    dateInput.value = preferred;
    syncBookDateChips(preferred);
  }

  function selectBookDate(iso) {
    const dateInput = $("#book-date");
    if (!dateInput || !iso) return;
    dateInput.value = iso;
    syncBookDateChips(iso);
  }

  function selectBookTime(value) {
    const timeInput = $("#book-time");
    if (!timeInput) return;
    if (value) timeInput.value = value;
    syncBookTimeChips(timeInput.value || "");
  }

  function openBookModal() {
    const modal = $("#book-modal");
    const form = $("#book-form");
    const success = $("#book-success");
    const status = $("#book-status");
    if (!modal) return;

    if (form) form.hidden = false;
    if (success) success.hidden = true;
    if (status) setStatus(status, "");

    const email = $("#book-email");
    const phone = $("#book-phone");
    const timeInput = $("#book-time");
    if (email && state.email) email.value = state.email;
    if (phone && state.phone) {
      const digits = state.phone.replace(/\D/g, "");
      phone.value = digits.startsWith("92") ? digits.slice(2) : digits.replace(/^0/, "");
    }

    buildBookDates($("#book-date")?.value || "");
    if (timeInput && !timeInput.value) timeInput.value = "10:00";
    selectBookTime(timeInput?.value || "10:00");

    modal.hidden = false;
    document.body.classList.add("book-open");
    $("#book-name")?.focus();
  }

  function closeBookModal() {
    const modal = $("#book-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("book-open");
  }

  function initBookingModal() {
    $("#open-book-modal")?.addEventListener("click", openBookModal);

    $$("[data-book-close]").forEach((el) => {
      el.addEventListener("click", closeBookModal);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#book-modal")?.hidden) closeBookModal();
    });

    $("#book-date")?.addEventListener("change", (e) => {
      syncBookDateChips(e.target.value);
    });

    $("#book-time")?.addEventListener("input", (e) => {
      syncBookTimeChips(e.target.value);
    });

    $("#book-date-strip")?.addEventListener("click", (e) => {
      const chip = e.target.closest(".book-date-chip");
      if (!chip) return;
      selectBookDate(chip.dataset.date);
    });

    $("#book-time-grid")?.addEventListener("click", (e) => {
      const slot = e.target.closest(".book-time-slot");
      if (!slot) return;
      selectBookTime(slot.dataset.time);
    });

    $("#book-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const name = $("#book-name")?.value.trim() || "";
      const email = $("#book-email")?.value.trim() || "";
      const phoneRaw = $("#book-phone")?.value.trim() || "";
      const date = $("#book-date")?.value || "";
      const time = $("#book-time")?.value || "";
      const note = $("#book-note")?.value.trim() || "";
      const status = $("#book-status");

      if (!name || !email || !date || !time) {
        setStatus(status, "Please complete all required fields.", true);
        return;
      }
      if (!isValidPakistaniMobile(phoneRaw)) {
        setStatus(status, "Enter a valid Pakistani mobile number.", true);
        return;
      }

      const phone = formatPakistaniPhone(phoneRaw);
      const timeLabel = formatBookTimeLabel(time);
      const dateLabel = new Date(`${date}T12:00:00`).toLocaleDateString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      });

      const subject = encodeURIComponent("Virtual Smile Assessment booking");
      const body = encodeURIComponent(
        [
          `Name: ${name}`,
          `Email: ${email}`,
          `Phone: ${phone}`,
          `Preferred date: ${dateLabel}`,
          `Preferred time: ${timeLabel}`,
          note ? `Note: ${note}` : "",
          "",
          "Requested via Virtual Smile Assessment.",
        ]
          .filter(Boolean)
          .join("\n")
      );

      const mail = document.createElement("a");
      mail.href = `mailto:hello@theglobaldentist.com?subject=${subject}&body=${body}`;
      mail.click();

      const form = $("#book-form");
      const success = $("#book-success");
      const copy = $("#book-success-copy");
      if (form) form.hidden = true;
      if (success) success.hidden = false;
      if (copy) {
        copy.textContent = `Thanks ${name.split(" ")[0]}. We’ve noted ${dateLabel} at ${timeLabel}. Check your email app to send the request, or wait for our confirmation.`;
      }
    });
  }

  document.querySelectorAll("[data-go]").forEach((el) => {
    el.addEventListener("click", (e) => {
      const go = el.getAttribute("data-go");
      if (!go) return;
      e.preventDefault();
      showAppFlow(go);
    });
  });

  const brand = document.querySelector(".brand-mark");
  if (brand) {
    brand.addEventListener("click", (e) => {
      e.preventDefault();
      showHeroOnly();
    });
  }

  $("#start-over").addEventListener("click", () => {
    state.frontFile = null;
    state.leftFile = null;
    state.rightFile = null;
    state.reportText = "";
    state.findings = null;
    state.simulationAllowed = true;
    setPhotosLocked(false);
    resetChatLimit();
    ["#front-image", "#left-image", "#right-image"].forEach((sel) => {
      const input = $(sel);
      const tile = input.closest(".upload-tile");
      const key = tile?.dataset.upload === "front" ? "frontFile" : tile?.dataset.upload === "left" ? "leftFile" : "rightFile";
      if (tile && input) clearUploadTile(tile, input, key);
    });
    $("#run-analysis").disabled = true;
    const simBlock = $("#sim-block");
    if (simBlock) simBlock.hidden = false;
    const simSkipNote = $("#sim-skip-note");
    if (simSkipNote) simSkipNote.hidden = true;
    showStep(1);
    showHeroOnly();
  });

  wireUploads();
  showStep(1);
  showHeroOnly();
  initHeroSlider();
  initBookingModal();
})();
