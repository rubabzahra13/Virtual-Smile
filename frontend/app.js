(() => {
  const state = {
    email: "",
    phone: "",
    assessmentId: null,
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

  function resetAssessScroll() {
    const root = document.documentElement;
    const body = document.body;
    const assess = $("#assess");
    const touched = [root, body, assess].filter(Boolean);
    $$(".hero-assess .panel").forEach((panel) => touched.push(panel));
    const shell = document.querySelector(".hero-shell.is-assessing");
    if (shell) touched.push(shell);
    const card = document.querySelector(".hero-shell.is-assessing .hero-card");
    if (card) touched.push(card);

    const prev = touched.map((el) => el.style.scrollBehavior);
    touched.forEach((el) => {
      el.style.scrollBehavior = "auto";
    });

    window.scrollTo(0, 0);
    root.scrollTop = 0;
    body.scrollTop = 0;
    touched.forEach((el) => {
      el.scrollTop = 0;
    });

    touched.forEach((el, i) => {
      el.style.scrollBehavior = prev[i] || "";
    });
  }

  function showStep(step) {
    // Zero scroll before revealing the next panel so we never land mid-page.
    resetAssessScroll();
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
      if (active) panel.scrollTop = 0;
    });
    setChatFloatVisible(step === 3);
    resetAssessScroll();
  }

  function setPhotosLocked(locked) {
    state.photosLocked = !!locked;
    $$(".upload-tile").forEach((tile) => {
      const removeBtn = tile.querySelector(".upload-remove");
      const openBtn = tile.querySelector("[data-open-photo-source]");
      tile.querySelectorAll(".upload-input").forEach((input) => {
        input.disabled = state.photosLocked;
      });
      tile.classList.toggle("is-locked", state.photosLocked);
      if (openBtn) openBtn.disabled = state.photosLocked;
      if (removeBtn) {
        if (state.photosLocked) {
          removeBtn.hidden = true;
          removeBtn.disabled = true;
        } else if (tile.classList.contains("has-file")) {
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
    tile.querySelectorAll(".upload-input").forEach((el) => {
      el.value = "";
    });
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

  let pendingPhotoTile = null;
  let cameraStream = null;
  let cameraTile = null;
  let cameraFacing = "user"; // default front camera

  function uploadKeyForTile(tile) {
    const kind = tile?.dataset.upload;
    if (kind === "left") return "leftFile";
    if (kind === "right") return "rightFile";
    return "frontFile";
  }

  function updateCameraFlipLabel() {
    const label = $("#camera-flip-label");
    const flip = $("#camera-flip");
    const isFront = cameraFacing === "user";
    if (label) label.textContent = isFront ? "Front" : "Back";
    if (flip) {
      flip.setAttribute("aria-label", isFront ? "Switch to back camera" : "Switch to front camera");
    }
  }

  async function startCameraStream(facing) {
    const video = $("#camera-video");
    const status = $("#camera-status");
    const shutter = $("#camera-shutter");
    const mode = facing === "environment" ? "environment" : "user";
    cameraFacing = mode;
    updateCameraFlipLabel();

    const attempts = [
      { video: { facingMode: { exact: mode }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
      { video: { facingMode: { ideal: mode }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
      { video: { facingMode: mode }, audio: false },
      { video: true, audio: false },
    ];

    let lastError = null;
    for (const constraints of attempts) {
      try {
        stopCameraStream();
        cameraStream = await navigator.mediaDevices.getUserMedia(constraints);
        if (video) {
          video.srcObject = cameraStream;
          video.classList.toggle("is-mirrored", cameraFacing === "user");
          await video.play().catch(() => {});
        }
        if (status) status.textContent = "";
        if (shutter) shutter.disabled = false;
        return true;
      } catch (err) {
        lastError = err;
      }
    }

    if (status) {
      status.textContent =
        lastError?.name === "NotAllowedError"
          ? "Camera permission blocked. Allow camera access, or upload from your library."
          : "Could not open camera. Try uploading from your library instead.";
    }
    if (shutter) shutter.disabled = true;
    return false;
  }

  function closePhotoSourceSheet() {
    const sheet = $("#photo-source-sheet");
    if (sheet) sheet.hidden = true;
    pendingPhotoTile = null;
    document.body.classList.remove("photo-source-open");
  }

  function openPhotoSourceSheet(tile) {
    if (!tile || state.photosLocked || tile.classList.contains("has-file")) return;
    pendingPhotoTile = tile;
    const sheet = $("#photo-source-sheet");
    const title = $("#photo-source-title");
    if (title) {
      const label = tile.querySelector(".upload-title")?.childNodes?.[0]?.textContent?.trim() || "photo";
      title.textContent = `Add ${label.toLowerCase()}`;
    }
    if (sheet) sheet.hidden = false;
    document.body.classList.add("photo-source-open");
  }

  function stopCameraStream() {
    if (cameraStream) {
      cameraStream.getTracks().forEach((track) => track.stop());
      cameraStream = null;
    }
    const video = $("#camera-video");
    if (video) {
      video.srcObject = null;
    }
  }

  function closeCameraCapture() {
    stopCameraStream();
    cameraTile = null;
    cameraFacing = "user";
    const modal = $("#camera-capture");
    const status = $("#camera-status");
    const shutter = $("#camera-shutter");
    if (modal) modal.hidden = true;
    if (status) status.textContent = "";
    if (shutter) shutter.disabled = true;
    document.body.classList.remove("camera-open");
    updateCameraFlipLabel();
  }

  async function openCameraCapture(tile) {
    if (!tile || state.photosLocked) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      tile.querySelector(".upload-input--camera")?.click();
      return;
    }

    cameraTile = tile;
    cameraFacing = "user";
    const modal = $("#camera-capture");
    const status = $("#camera-status");
    const shutter = $("#camera-shutter");
    const title = $("#camera-capture-title");
    const hint = $("#camera-capture-hint");
    const label = tile.querySelector(".upload-title")?.childNodes?.[0]?.textContent?.trim() || "photo";

    if (title) title.textContent = `Take ${label.toLowerCase()}`;
    if (hint) {
      hint.textContent =
        tile.dataset.upload === "front"
          ? "Face the camera and smile, then capture."
          : "Hold a side angle of your smile, then capture.";
    }
    if (status) status.textContent = "Starting camera…";
    if (shutter) shutter.disabled = true;
    if (modal) modal.hidden = false;
    document.body.classList.add("camera-open");
    updateCameraFlipLabel();
    await startCameraStream("user");
  }

  async function flipCamera() {
    const next = cameraFacing === "user" ? "environment" : "user";
    const status = $("#camera-status");
    const flip = $("#camera-flip");
    if (status) status.textContent = next === "user" ? "Switching to front…" : "Switching to back…";
    if (flip) flip.disabled = true;
    await startCameraStream(next);
    if (flip) flip.disabled = false;
  }

  function captureCameraPhoto() {
    const tile = cameraTile;
    const video = $("#camera-video");
    const canvas = $("#camera-canvas");
    const status = $("#camera-status");
    if (!tile || !video || !canvas || !cameraStream) return;
    if (!video.videoWidth || !video.videoHeight) {
      if (status) status.textContent = "Camera is still starting. Try again in a moment.";
      return;
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    // Mirror capture when using front camera so it matches the mirrored preview.
    if (cameraFacing === "user") {
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        if (!blob) {
          if (status) status.textContent = "Could not capture photo. Please try again.";
          return;
        }
        const gallery = tile.querySelector(".upload-input--gallery");
        const key = uploadKeyForTile(tile);
        const file = new File([blob], `${tile.dataset.upload || "smile"}-capture.jpg`, {
          type: "image/jpeg",
        });
        try {
          const dt = new DataTransfer();
          dt.items.add(file);
          if (gallery) gallery.files = dt.files;
        } catch (_) {
          /* state still holds the File */
        }
        setUploadTileFile(tile, gallery || tile.querySelector(".upload-input"), key, file);
        closeCameraCapture();
      },
      "image/jpeg",
      0.92
    );
  }

  function wireUploads() {
    [
      ["#front-image", "frontFile"],
      ["#left-image", "leftFile"],
      ["#right-image", "rightFile"],
    ].forEach(([inputSel, key]) => {
      const gallery = $(inputSel);
      if (!gallery) return;
      const tile = gallery.closest(".upload-tile");
      if (!tile) return;
      const camera = tile.querySelector(".upload-input--camera");
      const removeBtn = tile.querySelector(".upload-remove");
      const openBtn = tile.querySelector("[data-open-photo-source]");

      const onFile = (input) => {
        if (state.photosLocked) {
          input.value = "";
          return;
        }
        const file = input.files?.[0] || null;
        if (file && input !== gallery) {
          try {
            const dt = new DataTransfer();
            dt.items.add(file);
            gallery.files = dt.files;
          } catch (_) {
            /* some browsers block assigning FileList; state still holds the File */
          }
        }
        setUploadTileFile(tile, gallery, key, file);
        closePhotoSourceSheet();
      };

      gallery.addEventListener("change", () => onFile(gallery));
      if (camera) camera.addEventListener("change", () => onFile(camera));

      if (openBtn) {
        openBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          openPhotoSourceSheet(tile);
        });
      }

      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (state.photosLocked) return;
          clearUploadTile(tile, gallery, key);
        });
      }
    });

    const sheet = $("#photo-source-sheet");
    if (sheet) {
      sheet.querySelectorAll("[data-photo-source-close]").forEach((el) => {
        el.addEventListener("click", closePhotoSourceSheet);
      });

      sheet.querySelectorAll("[data-photo-source]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const tile = pendingPhotoTile;
          if (!tile || state.photosLocked) {
            closePhotoSourceSheet();
            return;
          }
          const source = btn.getAttribute("data-photo-source");
          closePhotoSourceSheet();
          if (source === "camera") {
            requestAnimationFrame(() => openCameraCapture(tile));
            return;
          }
          requestAnimationFrame(() => tile.querySelector(".upload-input--gallery")?.click());
        });
      });
    }

    $$("[data-camera-close]").forEach((el) => {
      el.addEventListener("click", closeCameraCapture);
    });
    $("#camera-shutter")?.addEventListener("click", captureCameraPhoto);
    $("#camera-flip")?.addEventListener("click", () => {
      flipCamera();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!$("#camera-capture")?.hidden) {
        closeCameraCapture();
        return;
      }
      if (!$("#photo-source-sheet")?.hidden) closePhotoSourceSheet();
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

  const ALREADY_ASSESSED_MSG =
    "You have already taken an assessment. Please use a different email or mobile number, or contact the clinic.";

  let eligibilityBlock = null; // { field: 'email'|'phone', reason: string }

  function clearEligibilityBlock() {
    eligibilityBlock = null;
    const status = $("#details-status");
    if (status) setStatus(status, "");
  }

  function applyEligibilityBlock(field, reason) {
    const friendly =
      reason ||
      (field === "phone"
        ? "You have already taken an assessment with this mobile number."
        : "You have already taken an assessment with this email.");
    eligibilityBlock = { field: field === "phone" ? "phone" : "email", reason: friendly };
    const status = $("#details-status");
    if (status) setStatus(status, ALREADY_ASSESSED_MSG, true);
  }

  function looksLikeAlreadyAssessed(text) {
    return /already taken an assessment/i.test(String(text || ""));
  }

  async function fetchEligibility(email, phone) {
    const url = `/api/eligibility?email=${encodeURIComponent(email)}&phone=${encodeURIComponent(phone)}`;
    let lastFailure = null;

    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const res = await fetch(url);
        const raw = await res.text();
        let data = {};
        try {
          data = raw ? JSON.parse(raw) : {};
        } catch (_parseErr) {
          data = {};
        }

        if (data && data.ok === false) {
          return { status: "blocked", data };
        }

        const detail = typeof data.detail === "string" ? data.detail : "";
        if (looksLikeAlreadyAssessed(detail)) {
          return {
            status: "blocked",
            data: {
              ok: false,
              field: /mobile|phone/i.test(detail) ? "phone" : "email",
              reason: detail,
            },
          };
        }

        if (res.ok) {
          return { status: "ok", data };
        }

        lastFailure = {
          status: "error",
          message: detail || "Could not verify your details. Please try again.",
          httpStatus: res.status,
        };
      } catch (_err) {
        lastFailure = {
          status: "error",
          message: "Could not verify your details. Please try again.",
        };
      }

      if (attempt < 3) {
        await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
      }
    }

    return lastFailure || {
      status: "error",
      message: "Could not verify your details. Please try again.",
    };
  }

  $("#details-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearEligibilityBlock();
    updateContinueEnabled(true);
    const btn = $("#continue-to-photos");
    if (btn?.disabled) return;

    const email = $("#user-email").value.trim();
    const phone = formatPakistaniPhone($("#user-phone").value);
    btn.disabled = true;
    const prevLabel = btn.textContent;
    btn.textContent = "Checking…";
    try {
      const result = await fetchEligibility(email, phone);
      if (result.status === "blocked") {
        applyEligibilityBlock(result.data?.field, result.data?.reason);
        btn.textContent = prevLabel;
        updateContinueEnabled(true);
        return;
      }
      if (result.status === "error") {
        const status = $("#details-status");
        if (status) setStatus(status, result.message || ALREADY_ASSESSED_MSG, true);
        btn.textContent = prevLabel;
        updateContinueEnabled(true);
        return;
      }
      state.email = email;
      state.phone = phone;
      btn.textContent = prevLabel;
      showStep(2);
    } catch (_err) {
      const status = $("#details-status");
      if (status) {
        setStatus(status, "Could not verify your details. Please try again.", true);
      }
      btn.textContent = prevLabel;
      updateContinueEnabled(true);
    }
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

    if (eligibilityBlock?.field === "email" && emailOk) {
      emailMsg = eligibilityBlock.reason;
      emailOk = false;
    }
    if (eligibilityBlock?.field === "phone" && phoneOk) {
      phoneMsg = eligibilityBlock.reason;
      phoneOk = false;
    }

    setFieldFeedback("#email-feedback", emailMsg, emailOk && !emailMsg);
    setFieldFeedback("#phone-feedback", phoneMsg, phoneOk && !phoneMsg);
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
    el.addEventListener("input", () => {
      clearEligibilityBlock();
      updateContinueEnabled(false);
    });
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
      const raw = await res.text();
      let data = {};
      try {
        data = raw ? JSON.parse(raw) : {};
      } catch (_e) {
        data = {};
      }
      if (!res.ok) {
        const detail = data.detail;
        const msg = typeof detail === "string"
          ? detail
          : detail?.message || (raw && raw.trim() ? raw.trim() : "Server error during analysis.");
        throw new Error(msg);
      }

      state.reportText = data.report_text || "";
      state.findings = data.findings || null;
      state.overallScore = data.overall_score;
      state.rawOutput = data.raw_model_output || "";
      state.simulationAllowed = data.simulation_allowed !== false;
      state.categoryScores = extractCategoryScores(data, state.reportText);
      state.assessmentId = data.assessment_id || null;

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
    const shell = document.querySelector(".hero-shell");
    const landing = $("#hero-landing");
    const assess = $("#assess");
    const orb = document.querySelector(".hero-orb-cta");
    // Keep the landing hero exactly as-is; only reveal About below.
    if (shell) shell.classList.remove("is-assessing");
    if (landing) landing.hidden = false;
    if (assess) assess.hidden = true;
    if (orb) orb.hidden = false;
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
  const heroMobileMq = window.matchMedia("(max-width: 520px)");

  function isHeroSliderEnabled() {
    return heroMobileMq.matches;
  }

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

    if (!isHeroSliderEnabled()) {
      heroSlideIndex = 0;
      updateHeroSlideState(0);
      track.style.transform = "none";
      return;
    }

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
    if (!isHeroSliderEnabled()) return;
    if (heroSlideIndex >= heroRealCount) {
      setHeroSlide(heroSlideIndex % heroRealCount, { instant: true });
    }
    setHeroSlide(heroSlideIndex + 1, { user });
  }

  function goHeroToLogical(logical, { user = false } = {}) {
    if (!isHeroSliderEnabled()) return;
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
    if (!isHeroSliderEnabled()) {
      pauseHeroSlider();
      const track = $("#hero-track");
      heroSlideIndex = 0;
      updateHeroSlideState(0);
      if (track) track.style.transform = "none";
      return;
    }
    heroSliderPaused = false;
    restartHeroSlider();
  }

  function restartHeroSlider() {
    if (heroSlideTimer) clearInterval(heroSlideTimer);
    if (!isHeroSliderEnabled()) return;
    if (heroSliderPaused) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    heroSlideTimer = setInterval(() => {
      goHeroNext();
    }, 3400);
  }

  function syncHeroSliderMode() {
    const slider = $("#hero-landing");
    const track = $("#hero-track");
    if (!slider || !track) return;

    if (isHeroSliderEnabled()) {
      slider.classList.add("is-slider-active");
      slider.setAttribute("aria-roledescription", "carousel");
      setHeroSlide(0, { instant: true });
      if (!$(".hero-shell")?.classList.contains("is-assessing")) {
        resumeHeroSlider();
      }
    } else {
      slider.classList.remove("is-slider-active");
      slider.removeAttribute("aria-roledescription");
      pauseHeroSlider();
      heroSlideIndex = 0;
      updateHeroSlideState(0);
      track.style.transform = "none";
    }
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
      if (!isHeroSliderEnabled()) return;
      if (e.target !== track || e.propertyName !== "transform") return;
      if (heroTrackJumping) return;
      if (heroSlideIndex >= heroRealCount) {
        setHeroSlide(heroSlideIndex % heroRealCount, { instant: true });
      }
    });

    $$(".hero-dot").forEach((dot) => {
      dot.addEventListener("click", () => {
        if (!isHeroSliderEnabled()) return;
        goHeroToLogical(Number(dot.dataset.slideTo) || 0, { user: true });
      });
    });

    let startX = 0;
    let deltaX = 0;
    let swiping = false;

    slider.addEventListener(
      "touchstart",
      (e) => {
        if (!isHeroSliderEnabled() || !e.touches[0]) return;
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
        if (!isHeroSliderEnabled() || !swiping || !e.touches[0]) return;
        deltaX = e.touches[0].clientX - startX;
      },
      { passive: true }
    );

    slider.addEventListener("touchend", () => {
      if (!isHeroSliderEnabled() || !swiping) {
        swiping = false;
        return;
      }
      swiping = false;
      if (Math.abs(deltaX) > 42) {
        // Always advance forward (1 → 2 → 1), never reverse-wrap.
        goHeroNext({ user: true });
      } else {
        resumeHeroSlider();
      }
    });

    const onModeChange = () => syncHeroSliderMode();
    if (typeof heroMobileMq.addEventListener === "function") {
      heroMobileMq.addEventListener("change", onModeChange);
    } else if (typeof heroMobileMq.addListener === "function") {
      heroMobileMq.addListener(onModeChange);
    }

    syncHeroSliderMode();
  }

  function toLocalISODate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function formatBookTimeLabel(value) {
    if (!value) return "";
    const parsed = parseBookTime(value);
    if (!parsed) return String(value).trim();
    const { hours, minutes } = parsed;
    const suffix = hours >= 12 ? "PM" : "AM";
    const hour12 = hours % 12 || 12;
    return `${hour12}:${String(minutes).padStart(2, "0")} ${suffix}`;
  }

  /** Accepts "12:00 PM", "12:00PM", "14:30", "2:00 pm" → { hours, minutes } or null */
  function parseBookTime(raw) {
    const text = String(raw || "").trim();
    if (!text) return null;
    const ampm = text.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
    if (ampm) {
      let hours = Number(ampm[1]);
      const minutes = Number(ampm[2]);
      const suffix = ampm[3].toUpperCase();
      if (hours < 1 || hours > 12 || minutes > 59) return null;
      if (suffix === "AM") hours = hours === 12 ? 0 : hours;
      else hours = hours === 12 ? 12 : hours + 12;
      return { hours, minutes };
    }
    const h24 = text.match(/^(\d{1,2}):(\d{2})$/);
    if (h24) {
      const hours = Number(h24[1]);
      const minutes = Number(h24[2]);
      if (hours > 23 || minutes > 59) return null;
      return { hours, minutes };
    }
    return null;
  }

  function toHhmm(raw) {
    const parsed = parseBookTime(raw);
    if (!parsed) return "";
    return `${String(parsed.hours).padStart(2, "0")}:${String(parsed.minutes).padStart(2, "0")}`;
  }

  function resetBookTimeSelect(placeholder) {
    const timeInput = $("#book-time");
    if (!timeInput) return;
    timeInput.innerHTML = `<option value="">${placeholder || "Select a date first"}</option>`;
    timeInput.value = "";
    timeInput.disabled = true;
    timeInput.dataset.touched = "";
  }

  function fillBookTimeSelect(slots, preferred) {
    const timeInput = $("#book-time");
    if (!timeInput) return;
    if (!slots.length) {
      timeInput.innerHTML = `<option value="">No open times</option>`;
      timeInput.value = "";
      timeInput.disabled = true;
      return;
    }
    const preferredHhmm = toHhmm(preferred) || preferred;
    const selected = slots.includes(preferredHhmm) ? preferredHhmm : slots[0];
    timeInput.innerHTML =
      `<option value="">Select a time</option>` +
      slots
        .map(
          (slot) =>
            `<option value="${slot}" ${slot === selected ? "selected" : ""}>${formatBookTimeLabel(slot)}</option>`
        )
        .join("");
    timeInput.disabled = false;
    timeInput.value = selected;
  }

  function formatBookDateLabel(iso) {
    if (!iso) return "Select a date";
    return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  }

  function bookMinDate() {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  }

  function bookMaxDate() {
    const d = bookMinDate();
    return new Date(d.getFullYear() + 1, 11, 31, 12, 0, 0, 0);
  }

  function validateBookName(raw) {
    const name = String(raw || "").trim().replace(/\s+/g, " ");
    if (!name) return { ok: false, msg: "Full name is required." };
    if (name.length < 3) return { ok: false, msg: "Name must be at least 3 characters." };
    if (name.length > 60) return { ok: false, msg: "Name is too long (60 characters max)." };
    if (/\d/.test(name)) return { ok: false, msg: "Name can’t include numbers." };
    if (!/^[\p{L}][\p{L}\s'.-]*$/u.test(name)) {
      return { ok: false, msg: "Use letters only (spaces, hyphen, apostrophe OK)." };
    }
    const parts = name.split(" ").filter(Boolean);
    if (parts.length < 2) return { ok: false, msg: "Enter first and last name." };
    if (parts.some((part) => part.replace(/[^\p{L}]/gu, "").length < 2)) {
      return { ok: false, msg: "Each name part needs at least 2 letters." };
    }
    return { ok: true, msg: "" };
  }

  let bookCalMonth = null;
  let bookMonthMeta = {}; // iso -> { closed, full, free_count }
  let bookFreeSlots = []; // HH:MM for selected date
  let bookSubmitting = false;
  let existingBooking = null;

  function formatBookingSummary(booking) {
    if (!booking) return "";
    const day = formatBookDateLabel(booking.date);
    const time = formatBookTimeLabel(String(booking.time || "").slice(0, 5));
    return `${day} at ${time}`;
  }

  function showAlreadyBookedState(booking) {
    const form = $("#book-form");
    const success = $("#book-success");
    const copy = $("#book-success-copy");
    const status = $("#book-status");
    if (form) form.hidden = true;
    if (success) success.hidden = false;
    if (copy) {
      copy.textContent = `You already have an appointment for ${formatBookingSummary(booking)}. Contact the clinic if you need to change it.`;
    }
    if (status) setStatus(status, "");
  }

  async function fetchExistingBooking() {
    const email = state.email || "";
    const phone = state.phone || "";
    if (!email && !phone) return null;
    try {
      const res = await fetch(
        `/api/bookings/mine?email=${encodeURIComponent(email)}&phone=${encodeURIComponent(phone)}`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return null;
      if (data.booked && data.booking) return data.booking;
    } catch (_e) {
      /* ignore and allow booking form */
    }
    return null;
  }

  function closeBookPickers() {
    const cal = $("#book-cal");
    const dateTrigger = $("#book-date-trigger");
    if (cal) cal.hidden = true;
    if (dateTrigger) dateTrigger.setAttribute("aria-expanded", "false");
  }

  async function loadBookMonthMeta() {
    if (!bookCalMonth) return;
    const year = bookCalMonth.getFullYear();
    const month = bookCalMonth.getMonth() + 1;
    try {
      const res = await fetch(`/api/availability/month?year=${year}&month=${month}`);
      if (!res.ok) {
        bookMonthMeta = {};
        return;
      }
      const data = await res.json();
      bookMonthMeta = data.days || {};
    } catch (_e) {
      bookMonthMeta = {};
    }
  }

  async function loadBookFreeSlots(iso) {
    bookFreeSlots = [];
    const hint = $("#book-time-hint");
    const previous = $("#book-time")?.value || "";
    if (!iso) {
      resetBookTimeSelect("Select a date first");
      if (hint) hint.textContent = "Clinic hours are set by the practice.";
      return;
    }
    try {
      const res = await fetch(`/api/availability?date=${encodeURIComponent(iso)}`);
      if (!res.ok) {
        resetBookTimeSelect("Could not load times");
        if (hint) hint.textContent = "Could not load clinic hours. Try another date.";
        return;
      }
      const data = await res.json();
      let slots = Array.isArray(data.slots) ? data.slots : [];
      const todayIso = toLocalISODate(new Date());
      if (iso === todayIso) {
        const now = new Date();
        const currentHhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
        slots = slots.filter((s) => s > currentHhmm);
      }
      bookFreeSlots = slots;
      fillBookTimeSelect(bookFreeSlots, previous);
      if (hint) {
        if (data.closed || !bookFreeSlots.length) {
          hint.textContent = "Clinic closed on this date. Pick another day.";
        } else if (data.open_time && data.close_time) {
          hint.textContent = `Clinic hours: ${formatBookTimeLabel(data.open_time)} - ${formatBookTimeLabel(data.close_time)}`;
        } else {
          const first = formatBookTimeLabel(bookFreeSlots[0]);
          const last = formatBookTimeLabel(bookFreeSlots[bookFreeSlots.length - 1]);
          hint.textContent = `Clinic hours: ${first} - ${last}`;
        }
      }
    } catch (_e) {
      resetBookTimeSelect("Could not load times");
      if (hint) hint.textContent = "Could not load clinic hours. Try another date.";
    }
  }

  function timeMatchesFreeSlot(raw) {
    const hhmm = toHhmm(raw) || String(raw || "").trim();
    if (!hhmm || !bookFreeSlots.length) return false;
    return bookFreeSlots.includes(hhmm);
  }

  function syncBookCalSelectors() {
    const monthSelect = $("#book-cal-month-select");
    const yearSelect = $("#book-cal-year-select");
    if (!bookCalMonth || !monthSelect || !yearSelect) return;

    const min = bookMinDate();
    const max = bookMaxDate();
    const year = bookCalMonth.getFullYear();
    const month = bookCalMonth.getMonth();

    const months = Array.from({ length: 12 }, (_, i) =>
      new Date(2020, i, 1).toLocaleDateString(undefined, { month: "short" })
    );
    monthSelect.innerHTML = months
      .map((label, i) => {
        const probe = new Date(year, i, 1);
        const last = new Date(year, i + 1, 0);
        const disabled = last < min || probe > max;
        return `<option value="${i}" ${i === month ? "selected" : ""} ${disabled ? "disabled" : ""}>${label}</option>`;
      })
      .join("");

    const years = [];
    for (let y = min.getFullYear(); y <= max.getFullYear(); y += 1) years.push(y);
    yearSelect.innerHTML = years
      .map((y) => `<option value="${y}" ${y === year ? "selected" : ""}>${y}</option>`)
      .join("");

    const prev = $("#book-cal-prev");
    const next = $("#book-cal-next");
    if (prev) {
      const earlier = new Date(year, month - 1, 1);
      prev.disabled = new Date(earlier.getFullYear(), earlier.getMonth() + 1, 0) < min;
    }
    if (next) {
      const later = new Date(year, month + 1, 1);
      next.disabled = later > max;
    }
  }

  async function renderBookCalendar() {
    const grid = $("#book-cal-grid");
    const dateInput = $("#book-date");
    if (!grid || !bookCalMonth) return;

    syncBookCalSelectors();
    await loadBookMonthMeta();

    const year = bookCalMonth.getFullYear();
    const month = bookCalMonth.getMonth();
    const first = new Date(year, month, 1);
    const startPad = first.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const minIso = toLocalISODate(bookMinDate());
    const maxIso = toLocalISODate(bookMaxDate());
    const selected = dateInput?.value || "";
    const todayIso = toLocalISODate(new Date());

    const cells = [];
    for (let i = 0; i < startPad; i += 1) {
      cells.push('<span class="book-cal-day is-empty"></span>');
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      const iso = toLocalISODate(new Date(year, month, day));
      const meta = bookMonthMeta[iso] || {};
      const outOfRange = iso < minIso || iso > maxIso;
      const closedOrFull = !!meta.closed || !!meta.full;
      const sundayFallback = !Object.keys(bookMonthMeta).length && new Date(`${iso}T12:00:00`).getDay() === 0;
      const disabled = outOfRange || closedOrFull || sundayFallback;
      const classes = [
        "book-cal-day",
        selected === iso ? "is-selected" : "",
        iso === todayIso ? "is-today" : "",
        disabled ? "is-disabled" : "",
        meta.full ? "is-full" : "",
        meta.closed ? "is-closed" : "",
      ]
        .filter(Boolean)
        .join(" ");
      cells.push(
        `<button type="button" class="${classes}" data-date="${iso}" ${disabled ? "disabled" : ""} aria-pressed="${selected === iso ? "true" : "false"}">${day}</button>`
      );
    }
    grid.innerHTML = cells.join("");
  }

  async function selectBookDate(iso) {
    const dateInput = $("#book-date");
    const display = $("#book-date-display");
    const trigger = $("#book-date-trigger");
    if (!dateInput || !iso) return;
    dateInput.value = iso;
    if (display) display.textContent = formatBookDateLabel(iso);
    if (trigger) trigger.classList.add("has-value");
    closeBookPickers();
    await loadBookFreeSlots(iso);
    updateBookSubmitEnabled(false);
  }

  function selectBookTime(value) {
    const timeInput = $("#book-time");
    if (!timeInput) return;
    if (value && bookFreeSlots.includes(toHhmm(value) || value)) {
      timeInput.value = toHhmm(value) || value;
    }
    updateBookSubmitEnabled(false);
  }

  function buildBookTimeMenu() { /* slots filled from Clinic Hours API */ }

  function updateBookSubmitEnabled(showErrors = false) {
    const btn = $("#book-submit");
    const nameInput = $("#book-name");
    const emailInput = $("#book-email");
    const phoneInput = $("#book-phone");
    const dateInput = $("#book-date");
    const timeInput = $("#book-time");
    if (!btn || !nameInput || !emailInput || !phoneInput || !dateInput || !timeInput) return;

    const name = nameInput.value.trim().replace(/\s+/g, " ");
    const email = emailInput.value.trim();
    const phone = phoneInput.value.trim();
    const date = dateInput.value;
    const time = timeInput.value;
    const phoneDigits = normalizePakistaniMobile(phone);
    const nameCheck = validateBookName(name);

    let nameMsg = "";
    let emailMsg = "";
    let phoneMsg = "";
    let dateMsg = "";
    let timeMsg = "";
    let nameOk = false;
    let emailOk = false;
    let phoneOk = false;
    let dateOk = false;
    let timeOk = false;

    if (!nameCheck.ok) {
      nameMsg = showErrors || nameInput.dataset.touched === "1" ? nameCheck.msg : "";
      if (name) nameInput.dataset.touched = "1";
    } else {
      nameOk = true;
      if (nameInput.value !== name) nameInput.value = name;
    }

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

    if (!date) {
      dateMsg = showErrors || dateInput.dataset.touched === "1" ? "Please choose a date." : "";
    } else {
      dateOk = true;
    }

    if (!time) {
      timeMsg = showErrors || timeInput.dataset.touched === "1" ? "Please choose a time." : "";
    } else if (!date) {
      timeMsg = "Choose a date first.";
    } else if (!bookFreeSlots.length) {
      timeMsg = "No open times on this date.";
    } else if (!timeMatchesFreeSlot(time)) {
      timeMsg = "That time is not available. Pick an open slot.";
      timeInput.dataset.touched = "1";
    } else {
      timeOk = true;
    }

    setFieldFeedback("#book-name-feedback", nameMsg, nameOk);
    setFieldFeedback("#book-email-feedback", emailMsg, emailOk);
    setFieldFeedback("#book-phone-feedback", phoneMsg, phoneOk);
    setFieldFeedback("#book-date-feedback", dateMsg, dateOk);
    setFieldFeedback("#book-time-feedback", timeMsg, timeOk);

    nameInput.classList.toggle("is-invalid", !!nameMsg);
    emailInput.classList.toggle("is-invalid", !!emailMsg);
    phoneInput.classList.toggle("is-invalid", !!phoneMsg);
    nameInput.closest(".field")?.classList.toggle("is-invalid", !!nameMsg);
    emailInput.closest(".field")?.classList.toggle("is-invalid", !!emailMsg);
    phoneInput.closest(".field")?.classList.toggle("is-invalid", !!phoneMsg);
    $("#book-date-trigger")?.classList.toggle("is-invalid", !!dateMsg);
    $("#book-time")?.classList.toggle("is-invalid", !!timeMsg);

    btn.disabled = !(nameOk && emailOk && phoneOk && dateOk && timeOk);
  }

  async function openBookModal() {
    const modal = $("#book-modal");
    const form = $("#book-form");
    const success = $("#book-success");
    const status = $("#book-status");
    const openBtn = $("#open-book-modal");
    if (!modal) return;

    const prevBtnLabel = openBtn?.textContent || "Book an appointment";
    if (openBtn) {
      openBtn.disabled = true;
      openBtn.textContent = "Checking…";
    }

    bookSubmitting = false;
    closeBookPickers();

    try {
      // Resolve booking state before showing the modal so the form never flashes.
      if (!existingBooking) {
        existingBooking = await fetchExistingBooking();
      }

      if (form) form.hidden = true;
      if (success) success.hidden = true;
      if (status) setStatus(status, "");

      if (existingBooking) {
        showAlreadyBookedState(existingBooking);
        modal.hidden = false;
        document.body.classList.add("book-open");
        return;
      }

      const email = $("#book-email");
      const phone = $("#book-phone");
      if (email) {
        email.value = state.email || "";
        email.readOnly = true;
        email.disabled = true;
      }
      if (phone) {
        if (state.phone) {
          const digits = state.phone.replace(/\D/g, "");
          phone.value = digits.startsWith("92") ? digits.slice(2) : digits.replace(/^0/, "");
        } else {
          phone.value = "";
        }
        phone.readOnly = true;
        phone.disabled = true;
      }

      const min = bookMinDate();
      bookCalMonth = new Date(min.getFullYear(), min.getMonth(), 1);
      const dateInput = $("#book-date");
      const timeInput = $("#book-time");
      if (dateInput && !dateInput.value) {
        dateInput.value = "";
        $("#book-date-display").textContent = "Select a date";
        $("#book-date-trigger")?.classList.remove("has-value");
      } else if (dateInput?.value) {
        $("#book-date-display").textContent = formatBookDateLabel(dateInput.value);
        $("#book-date-trigger")?.classList.add("has-value");
        const selected = new Date(`${dateInput.value}T12:00:00`);
        bookCalMonth = new Date(selected.getFullYear(), selected.getMonth(), 1);
      }
      if (timeInput) {
        resetBookTimeSelect("Select a date first");
        const hint = $("#book-time-hint");
        if (hint) hint.textContent = "Clinic hours are set by the practice.";
      }

      if (form) form.hidden = false;
      if (success) success.hidden = true;
      modal.hidden = false;
      document.body.classList.add("book-open");
      $("#book-name")?.focus();

      await renderBookCalendar();
      if (dateInput?.value) await loadBookFreeSlots(dateInput.value);
      updateBookSubmitEnabled(false);
    } finally {
      if (openBtn) {
        openBtn.disabled = false;
        openBtn.textContent = prevBtnLabel;
      }
    }
  }

  function closeBookModal() {
    const modal = $("#book-modal");
    if (!modal) return;
    closeBookPickers();
    modal.hidden = true;
    document.body.classList.remove("book-open");
  }

  function initBookingModal() {
    $("#open-book-modal")?.addEventListener("click", openBookModal);

    $$("[data-book-close]").forEach((el) => {
      el.addEventListener("click", closeBookModal);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#book-modal")?.hidden) {
        if (!$("#book-cal")?.hidden) {
          closeBookPickers();
          return;
        }
        closeBookModal();
      }
    });

    ["#book-name"].forEach((sel) => {
      const el = $(sel);
      if (!el) return;
      el.addEventListener("input", () => updateBookSubmitEnabled(false));
      el.addEventListener("blur", () => {
        el.dataset.touched = "1";
        updateBookSubmitEnabled(false);
      });
    });

    // Email/phone are locked to the assessment details; do not wire edit handlers.

    $("#book-date-trigger")?.addEventListener("click", () => {
      const cal = $("#book-cal");
      const trigger = $("#book-date-trigger");
      if (!cal || !trigger) return;
      const open = cal.hidden;
      cal.hidden = !open;
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        $("#book-date").dataset.touched = "1";
        renderBookCalendar();
      }
    });

    $("#book-time")?.addEventListener("change", () => {
      const el = $("#book-time");
      if (el) el.dataset.touched = "1";
      updateBookSubmitEnabled(false);
    });

    $("#book-cal-prev")?.addEventListener("click", () => {
      if (!bookCalMonth) return;
      bookCalMonth = new Date(bookCalMonth.getFullYear(), bookCalMonth.getMonth() - 1, 1);
      const min = bookMinDate();
      if (new Date(bookCalMonth.getFullYear(), bookCalMonth.getMonth() + 1, 0) < min) {
        bookCalMonth = new Date(min.getFullYear(), min.getMonth(), 1);
      }
      renderBookCalendar();
    });

    $("#book-cal-next")?.addEventListener("click", () => {
      if (!bookCalMonth) return;
      bookCalMonth = new Date(bookCalMonth.getFullYear(), bookCalMonth.getMonth() + 1, 1);
      const max = bookMaxDate();
      if (bookCalMonth > max) {
        bookCalMonth = new Date(max.getFullYear(), max.getMonth(), 1);
      }
      renderBookCalendar();
    });

    $("#book-cal-month-select")?.addEventListener("change", (e) => {
      if (!bookCalMonth) return;
      const month = Number(e.target.value);
      bookCalMonth = new Date(bookCalMonth.getFullYear(), month, 1);
      renderBookCalendar();
    });

    $("#book-cal-year-select")?.addEventListener("change", (e) => {
      if (!bookCalMonth) return;
      const year = Number(e.target.value);
      bookCalMonth = new Date(year, bookCalMonth.getMonth(), 1);
      const min = bookMinDate();
      const max = bookMaxDate();
      if (new Date(year, bookCalMonth.getMonth() + 1, 0) < min) {
        bookCalMonth = new Date(min.getFullYear(), min.getMonth(), 1);
      } else if (bookCalMonth > max) {
        bookCalMonth = new Date(max.getFullYear(), max.getMonth(), 1);
      }
      renderBookCalendar();
    });

    $("#book-cal-grid")?.addEventListener("click", (e) => {
      const day = e.target.closest(".book-cal-day:not(.is-disabled):not(.is-empty)");
      if (!day) return;
      selectBookDate(day.dataset.date);
    });




    document.addEventListener("click", (e) => {
      if ($("#book-modal")?.hidden) return;
      if (e.target.closest(".book-picker-field")) return;
      closeBookPickers();
    });

    $("#book-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (bookSubmitting) return;
      if (existingBooking) {
        showAlreadyBookedState(existingBooking);
        return;
      }
      updateBookSubmitEnabled(true);
      const name = $("#book-name")?.value.trim() || "";
      const email = (state.email || $("#book-email")?.value.trim() || "");
      const phoneRaw = state.phone
        ? String(state.phone).replace(/^\+92/, "").replace(/\D/g, "").replace(/^92/, "")
        : ($("#book-phone")?.value.trim() || "");
      const date = $("#book-date")?.value || "";
      const time = $("#book-time")?.value || "";
      const note = $("#book-note")?.value.trim() || "";
      const status = $("#book-status");
      const submitBtn = $("#book-submit");

      if (submitBtn?.disabled) {
        setStatus(status, "Please complete all required fields.", true);
        return;
      }

      const phone = formatPakistaniPhone(phoneRaw);
      const timeLabel = formatBookTimeLabel(time);
      const dateLabel = formatBookDateLabel(date);
      const parsed = parseBookTime(time);
      const time24 = parsed
        ? `${String(parsed.hours).padStart(2, "0")}:${String(parsed.minutes).padStart(2, "0")}`
        : time;

      bookSubmitting = true;
      submitBtn.disabled = true;
      setStatus(status, "Booking your appointment…");
      try {
        const res = await fetch("/api/bookings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            email,
            phone,
            date,
            time: time24,
            note: note || null,
            assessment_id: state.assessmentId || null,
            source: "patient",
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = typeof data.detail === "string" ? data.detail : "Could not book that slot.";
          if (/already have an appointment/i.test(detail)) {
            existingBooking = await fetchExistingBooking();
            if (existingBooking) {
              showAlreadyBookedState(existingBooking);
              return;
            }
          }
          if (res.status === 409 && date) {
            await loadBookFreeSlots(date);
            await renderBookCalendar();
          }
          setStatus(status, detail, true);
          bookSubmitting = false;
          updateBookSubmitEnabled(true);
          return;
        }

        existingBooking = data;
        const form = $("#book-form");
        const success = $("#book-success");
        const copy = $("#book-success-copy");
        if (form) form.hidden = true;
        if (success) success.hidden = false;
        if (copy) {
          copy.textContent = `Thanks ${name.split(" ")[0]}. You’re booked for ${dateLabel} at ${timeLabel}. We’ll confirm by email shortly.`;
        }
        setStatus(status, "");
      } catch (_err) {
        setStatus(status, "Network error. Please try again.", true);
        bookSubmitting = false;
        updateBookSubmitEnabled(false);
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

  wireUploads();
  showStep(1);
  showHeroOnly();
  initHeroSlider();
  initBookingModal();
})();
