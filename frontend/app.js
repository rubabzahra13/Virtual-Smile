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
  };

  const FREE_CHAT_LIMIT = 5;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  function showStep(step) {
    $$(".flow-tab").forEach((tab) => {
      const n = Number(tab.dataset.step);
      tab.classList.toggle("is-active", n === step);
      if (n <= step) tab.disabled = false;
    });
    $$(".panel").forEach((panel) => {
      const active = Number(panel.dataset.panel) === step;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
  }

  function clearUploadTile(tile, input, key) {
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
        const file = input.files?.[0] || null;
        setUploadTileFile(tile, input, key, file);
      });

      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          clearUploadTile(tile, input, key);
        });
      }
    });
  }

  function setStatus(el, message, isError = false) {
    el.textContent = message || "";
    el.classList.toggle("is-error", !!isError);
  }

  function parseCategoryScores(reportText) {
    const scores = {};
    const map = {
      alignment: /Alignment:\s*(\d+)/i,
      gum_health: /Gum Health:\s*(\d+)/i,
      color: /Color:\s*(\d+)/i,
      restorations: /Restorations:\s*(\d+)/i,
      missing_teeth: /Missing Teeth:\s*(\d+)/i,
    };
    for (const [key, re] of Object.entries(map)) {
      const m = reportText.match(re);
      if (m) scores[key] = Number(m[1]);
    }
    return Object.keys(scores).length ? scores : null;
  }

  function formatConcernLabel(label) {
    return String(label || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (m) => m.toUpperCase());
  }

  function scoreBand(score) {
    if (score >= 90) return { tone: "good", label: "Good" };
    if (score >= 75) return { tone: "watch", label: "Monitor" };
    return { tone: "attention", label: "Needs attention" };
  }

  function renderCategories(scores) {
    const host = $("#category-scores");
    host.innerHTML = "";
    if (!scores) return;
    const labels = {
      alignment: "Alignment",
      gum_health: "Gum health",
      color: "Tooth colour",
      restorations: "Restorations",
      missing_teeth: "Missing teeth",
    };
    const icons = {
      alignment: "🧭",
      gum_health: "🦷",
      color: "✨",
      restorations: "🛠️",
      missing_teeth: "🧩",
    };
    Object.entries(scores).forEach(([key, value]) => {
      const band = scoreBand(Number(value || 0));
      const row = document.createElement("div");
      row.className = `cat-row cat-row-${band.tone}`;
      row.innerHTML = `
        <div class="cat-name"><span class="cat-icon">${icons[key] || "•"}</span>${labels[key] || key}</div>
        <div class="cat-bar"><i style="width:0%"></i></div>
        <span class="cat-score">${value}</span>
        <span class="cat-band">${band.label}</span>
      `;
      host.appendChild(row);
      requestAnimationFrame(() => {
        row.querySelector("i").style.width = `${Math.max(0, Math.min(100, value))}%`;
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
        "We found some visible areas you may want to discuss with your dentist.";
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
          <h5>${formatConcernLabel(c)}</h5>
          <p><strong>What this means:</strong> ${detail?.likely_cause || "This was visible in the photo and may need a professional check."}</p>
          <p><strong>Possible next steps:</strong> ${options.length ? options.join(", ") : "A dental consultation to confirm and plan treatment."}</p>
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
    label.textContent = typeof score === "number" ? score : "-";
    circle.style.strokeDasharray = `${circumference}`;
    circle.style.strokeDashoffset = `${circumference * (1 - safe / 100)}`;
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
    const input = $("#chat-input");
    const submit = $("#chat-submit") || $("#chat-form button[type='submit']");
    const remaining = chatRemaining();

    if (!note || !input) return;

    if (remaining <= 0) {
      note.textContent =
        "You’ve used all 5 free questions for this assessment. For more personalised advice, please book a consultation with our dental team.";
      note.classList.add("is-locked");
      input.disabled = true;
      input.placeholder = "Free questions used up for this assessment";
      input.required = false;
      if (submit) submit.disabled = true;
      return;
    }

    note.classList.remove("is-locked");
    input.disabled = false;
    input.required = true;
    input.placeholder = "e.g. Would aligners work for me?";
    if (submit) submit.disabled = false;

    if (remaining === FREE_CHAT_LIMIT) {
      note.textContent = `You have ${remaining} free questions included with this assessment.`;
    } else if (remaining === 1) {
      note.textContent = "You have 1 free question left.";
    } else {
      note.textContent = `You have ${remaining} free questions left.`;
    }
  }

  function resetChatLimit() {
    state.chatUsed = 0;
    state.chatHistory = [];
    updateChatLimitUI();
  }

  $("#details-form").addEventListener("submit", (e) => {
    e.preventDefault();
    state.email = $("#user-email").value.trim();
    state.phone = $("#user-phone").value.trim();
    if (!state.email || !state.phone) return;
    showStep(2);
  });

  $("#back-to-details").addEventListener("click", () => showStep(1));

  $("#run-analysis").addEventListener("click", async () => {
    if (!state.frontFile) return;
    const status = $("#analyze-status");
    const btn = $("#run-analysis");
    btn.disabled = true;
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
      state.categoryScores =
        data.category_scores || parseCategoryScores(state.reportText);

      $("#report-text").textContent = state.reportText;
      $("#results-summary").textContent = data.parsed_ok
        ? "Here’s your preliminary visual assessment and Smile Score."
        : "We generated a report, but some scoring fields could not be parsed cleanly.";
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
        "Hi - I’ve reviewed your assessment. Ask me anything about your Smile Score or suggested next steps. You have 5 free questions included.",
        "bot"
      );

      showStep(3);
      setStatus(status, "");
    } catch (err) {
      setStatus(status, err.message || "Analysis failed.", true);
    } finally {
      btn.disabled = !state.frontFile;
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
    document.body.classList.remove("flow-open");
    window.scrollTo({ top: 0, behavior: "smooth" });
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
})();
