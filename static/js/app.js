/* =====================================================================
   AI Avatar Studio - frontend logic (vanilla JavaScript, no framework)

   Sections:
     1. small helpers
     2. API wrapper
     3. generator page: uploads, counters, validation
     4. generator page: submit + status polling
     5. history page
     6. auth pages
   ===================================================================== */

(function () {
  "use strict";

  var CONFIG = window.APP_CONFIG || {};
  var TOKEN_KEY = "aas_access_token";

  /* ---------- 1. helpers ------------------------------------------ */

  function $(id) { return document.getElementById(id); }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var i = Math.floor(Math.log(bytes) / Math.log(1024));
    i = Math.min(i, units.length - 1);
    return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
  }

  function formatClock(totalSeconds) {
    var seconds = Math.max(0, Math.round(totalSeconds || 0));
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function formatDuration(seconds) {
    if (!seconds) return "—";
    if (seconds < 60) return seconds + " sec";
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return s ? m + " min " + s + " sec" : m + " min";
  }

  function formatDate(value) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function countWords(text) {
    var matches = (text || "").trim().match(/[\w'’-]+/g);
    return matches ? matches.length : 0;
  }

  var toastTimer = null;
  function toast(message, isError) {
    var el = $("toast");
    if (!el) return;
    el.textContent = message;
    el.classList.toggle("is-error", !!isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 5000);
  }

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }

  function setToken(token) {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* private browsing */ }
  }

  /* ---------- 2. API wrapper --------------------------------------- */

  /**
   * Call the FastAPI backend. Always resolves to parsed JSON, or throws an
   * Error whose message is the friendly "detail" string from the server.
   */
  async function api(path, options) {
    options = options || {};
    var headers = options.headers || {};

    var token = getToken();
    if (CONFIG.authEnabled && token) {
      headers["Authorization"] = "Bearer " + token;
    }

    var response;
    try {
      response = await fetch(path, {
        method: options.method || "GET",
        headers: headers,
        body: options.body
      });
    } catch (networkError) {
      throw new Error("Cannot reach the server. Is the backend still running?");
    }

    var data = null;
    var text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }

    if (!response.ok) {
      var detail = (data && (data.detail || data.message)) || "Something went wrong.";
      if (response.status === 401) {
        setToken(null);
      }
      throw new Error(detail);
    }
    return data;
  }

  /* ---------- 3. generator: uploads and validation ----------------- */

  var state = {
    imageFile: null,
    voiceFile: null,
    durationSeconds: 60,
    quality: "720p",
    consent: false,
    generationId: null,
    pollTimer: null
  };

  /**
   * Wire one drag-and-drop box to its hidden file input.
   */
  function setupDropzone(dropId, inputId, onFile) {
    var drop = $(dropId);
    var input = $(inputId);
    if (!drop || !input) return;

    drop.addEventListener("click", function () { input.click(); });

    drop.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });

    ["dragenter", "dragover"].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.add("is-over");
      });
    });

    ["dragleave", "drop"].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.remove("is-over");
      });
    });

    drop.addEventListener("drop", function (event) {
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) onFile(files[0]);
    });

    input.addEventListener("change", function () {
      if (input.files && input.files.length) onFile(input.files[0]);
    });
  }

  function extensionOf(name) {
    var dot = (name || "").lastIndexOf(".");
    return dot === -1 ? "" : name.slice(dot).toLowerCase();
  }

  function handleImage(file) {
    var allowed = [".jpg", ".jpeg", ".png", ".webp"];
    if (allowed.indexOf(extensionOf(file.name)) === -1) {
      toast("That image format is not supported. Use JPG, PNG or WEBP.", true);
      return;
    }
    if (file.size > CONFIG.maxImageMb * 1024 * 1024) {
      toast("That image is " + formatBytes(file.size) + ". The limit is " + CONFIG.maxImageMb + " MB.", true);
      return;
    }

    state.imageFile = file;
    $("image-name").textContent = file.name;
    $("image-size").textContent = formatBytes(file.size);
    $("image-preview").src = URL.createObjectURL(file);
    $("image-card").hidden = false;
    $("image-drop").hidden = true;
    refresh();
  }

  function handleVoice(file) {
    var allowed = [".wav", ".mp3", ".m4a"];
    if (allowed.indexOf(extensionOf(file.name)) === -1) {
      toast("That audio format is not supported. Use WAV, MP3 or M4A.", true);
      return;
    }
    if (file.size > CONFIG.maxVoiceMb * 1024 * 1024) {
      toast("That audio file is " + formatBytes(file.size) + ". The limit is " + CONFIG.maxVoiceMb + " MB.", true);
      return;
    }

    state.voiceFile = file;
    $("voice-name").textContent = file.name;
    $("voice-size").textContent = formatBytes(file.size);

    var player = $("voice-player");
    player.src = URL.createObjectURL(file);
    player.onloadedmetadata = function () {
      if (isFinite(player.duration)) {
        $("voice-size").textContent = formatBytes(file.size) + " · " + formatClock(player.duration);
      }
    };

    $("voice-card").hidden = false;
    $("voice-drop").hidden = true;
    refresh();
  }

  function clearImage() {
    state.imageFile = null;
    $("image-input").value = "";
    $("image-card").hidden = true;
    $("image-drop").hidden = false;
    refresh();
  }

  function clearVoice() {
    state.voiceFile = null;
    $("voice-input").value = "";
    $("voice-player").removeAttribute("src");
    $("voice-card").hidden = true;
    $("voice-drop").hidden = false;
    refresh();
  }

  /** Estimated speech length, matching the server's words-per-minute setting. */
  function estimatedSeconds() {
    var words = countWords($("script-input").value);
    if (!words) return 0;
    return Math.ceil((words / CONFIG.wordsPerMinute) * 60);
  }

  function readCustomDuration() {
    var value = parseInt($("custom-value").value, 10) || 0;
    var unit = $("custom-unit").value;
    return unit === "minutes" ? value * 60 : value;
  }

  /** Recompute counters, the summary panel and the Generate button. */
  function refresh() {
    var script = $("script-input").value;
    var words = countWords(script);
    var estimate = estimatedSeconds();

    $("word-count").textContent = words;
    $("char-count").textContent = script.length;
    $("estimate").textContent = formatClock(estimate);

    // Duration warning: the script and the requested length disagree.
    var note = $("duration-note");
    if (estimate && state.durationSeconds) {
      if (estimate < state.durationSeconds * 0.75 || estimate > state.durationSeconds * 1.25) {
        note.textContent =
          "Your script is estimated at " + estimate + " seconds, but you selected a " +
          state.durationSeconds + "-second video.";
        note.hidden = false;
      } else {
        note.hidden = true;
      }
    } else {
      note.hidden = true;
    }

    // Summary
    $("sum-image").textContent = state.imageFile ? state.imageFile.name : "Not selected";
    $("sum-voice").textContent = state.voiceFile ? state.voiceFile.name : "Not selected";
    $("sum-script").textContent = words ? words + " words" : "Empty";
    $("sum-duration").textContent = formatDuration(state.durationSeconds);
    $("sum-quality").textContent = state.quality;

    // Generate button
    var problems = [];
    if (!state.imageFile) problems.push("an image");
    if (!state.voiceFile) problems.push("a voice sample");
    if (!script.trim()) problems.push("a script");
    if (!state.durationSeconds || state.durationSeconds < CONFIG.minDuration) problems.push("a valid duration");
    if (!state.consent) problems.push("your confirmation");

    var button = $("generate-button");
    button.disabled = problems.length > 0;
    $("generate-hint").textContent = problems.length
      ? "Add " + problems.join(", ") + " to start."
      : "Ready. Generation runs in the background.";
  }

  function showPanel(name) {
    ["form", "progress", "result", "error"].forEach(function (key) {
      var panel = $("panel-" + key);
      if (panel) panel.hidden = key !== name;
    });
  }

  /* ---------- 4. generator: submit and polling --------------------- */

  async function submitGeneration() {
    var button = $("generate-button");
    button.disabled = true;

    var form = new FormData();
    form.append("image", state.imageFile);
    form.append("voice", state.voiceFile);
    form.append("text", $("script-input").value.trim());
    form.append("duration", String(state.durationSeconds));
    form.append("quality", state.quality);
    form.append("consent", state.consent ? "true" : "false");

    showPanel("progress");
    updateProgress(3, "Uploading files");

    try {
      var created = await api("/api/generate", { method: "POST", body: form });
      state.generationId = created.generation_id;
      startPolling();
    } catch (error) {
      showPanel("form");
      button.disabled = false;
      toast(error.message, true);
    }
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(checkStatus, 2000);
    checkStatus();
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function checkStatus() {
    if (!state.generationId) return;

    try {
      var status = await api("/api/status/" + state.generationId);
      updateProgress(status.progress, status.stage);

      if (status.status === "completed") {
        stopPolling();
        await showResult();
      } else if (status.status === "failed") {
        stopPolling();
        showFailure(status.error_message || "Video generation failed.");
      }
    } catch (error) {
      stopPolling();
      showFailure(error.message);
    }
  }

  function updateProgress(percent, stage) {
    percent = Math.max(0, Math.min(100, percent || 0));
    $("bar-fill").style.width = percent + "%";
    $("bar").setAttribute("aria-valuenow", percent);
    $("progress-percent").textContent = percent + "%";
    $("progress-stage").textContent = stage || "Working";

    // Mark the checklist: everything above the current stage is done.
    var items = $("stagelist").querySelectorAll("li");
    var currentIndex = -1;
    items.forEach(function (item, index) {
      if (item.dataset.stage === stage) currentIndex = index;
    });
    if (currentIndex === -1) {
      // Stages not in the list (for example "Generating facial expressions")
      // fall back to the progress percentage.
      currentIndex = Math.min(items.length - 1, Math.floor((percent / 100) * items.length));
    }

    items.forEach(function (item, index) {
      item.classList.toggle("is-done", index < currentIndex || percent >= 100);
      item.classList.toggle("is-active", index === currentIndex && percent < 100);
    });
  }

  async function showResult() {
    try {
      var video = await api("/api/video/" + state.generationId);
      $("result-video").src = video.video_url;
      $("result-duration").textContent = formatDuration(video.duration);
      $("result-quality").textContent = video.quality;
      $("result-date").textContent = formatDate(video.created_at);
      $("download-button").href = "/api/download/" + state.generationId;
      showPanel("result");
      toast("Your video is ready.");
    } catch (error) {
      showFailure(error.message);
    }
  }

  function showFailure(message) {
    $("error-text").textContent = message;
    showPanel("error");
  }

  function resetGenerator() {
    stopPolling();
    state.generationId = null;
    showPanel("form");
    refresh();
    document.getElementById("studio").scrollIntoView({ block: "start" });
  }

  function initGenerator() {
    if (!$("generate-button")) return;

    setupDropzone("image-drop", "image-input", handleImage);
    setupDropzone("voice-drop", "voice-input", handleVoice);

    $("image-remove").addEventListener("click", clearImage);
    $("voice-remove").addEventListener("click", clearVoice);
    $("script-input").addEventListener("input", refresh);

    // Duration chips
    var chips = $("duration-chips").querySelectorAll(".chip");
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        chips.forEach(function (other) {
          other.classList.remove("is-active");
          other.setAttribute("aria-checked", "false");
        });
        chip.classList.add("is-active");
        chip.setAttribute("aria-checked", "true");

        var value = chip.dataset.seconds;
        var isCustom = value === "custom";
        $("custom-duration").hidden = !isCustom;
        state.durationSeconds = isCustom ? readCustomDuration() : parseInt(value, 10);
        refresh();
      });
    });

    $("custom-value").addEventListener("input", function () {
      state.durationSeconds = readCustomDuration();
      refresh();
    });
    $("custom-unit").addEventListener("change", function () {
      state.durationSeconds = readCustomDuration();
      refresh();
    });

    $("quality-select").addEventListener("change", function (event) {
      state.quality = event.target.value;
      refresh();
    });

    $("consent-input").addEventListener("change", function (event) {
      state.consent = event.target.checked;
      refresh();
    });

    $("generate-button").addEventListener("click", submitGeneration);
    $("again-button").addEventListener("click", resetGenerator);
    $("retry-button").addEventListener("click", resetGenerator);

    state.quality = $("quality-select").value;
    refresh();
  }

  /* ---------- 5. history page -------------------------------------- */

  function statusClass(status) {
    if (status === "completed") return "status-completed";
    if (status === "failed") return "status-failed";
    return "status-working";
  }

  function statusLabel(status) {
    return status.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
  }

  function buildCard(item) {
    var card = document.createElement("article");
    card.className = "vcard";

    if (item.video_url) {
      var video = document.createElement("video");
      video.src = item.video_url;
      video.controls = true;
      video.preload = "metadata";
      video.playsInline = true;
      card.appendChild(video);
    } else {
      var placeholder = document.createElement("div");
      placeholder.className = "vcard-placeholder";
      placeholder.textContent =
        item.status === "failed" ? "No video — this generation failed" : "Still rendering…";
      card.appendChild(placeholder);
    }

    var body = document.createElement("div");
    body.className = "vcard-body";

    var script = document.createElement("p");
    script.className = "vcard-script";
    script.textContent = item.script_text || "(no script)";
    body.appendChild(script);

    var meta = document.createElement("div");
    meta.className = "vcard-meta";
    meta.innerHTML =
      "<span>" + formatDate(item.created_at) + "</span>" +
      "<span>" + formatDuration(item.duration) + "</span>" +
      "<span>" + (item.quality || "—") + "</span>";

    var badge = document.createElement("span");
    badge.className = "status " + statusClass(item.status);
    badge.textContent = statusLabel(item.status);
    meta.appendChild(badge);
    body.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "vcard-actions";

    if (item.video_url) {
      var download = document.createElement("a");
      download.className = "btn btn-outline btn-sm";
      download.href = "/api/download/" + item.id;
      download.textContent = "Download";
      download.setAttribute("download", "");
      actions.appendChild(download);
    }

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-quiet btn-sm";
    remove.textContent = "Delete";
    remove.addEventListener("click", async function () {
      if (!confirm("Delete this video and its files?")) return;
      try {
        await api("/api/generation/" + item.id, { method: "DELETE" });
        card.remove();
        toast("Video deleted.");
        if (!$("history-grid").children.length) {
          $("history-grid").hidden = true;
          $("history-empty").hidden = false;
        }
      } catch (error) {
        toast(error.message, true);
      }
    });
    actions.appendChild(remove);

    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  async function initHistory() {
    var grid = $("history-grid");
    if (!grid) return;

    try {
      var data = await api("/api/history");
      $("history-loading").hidden = true;

      if (!data.generations.length) {
        $("history-empty").hidden = false;
        return;
      }

      data.generations.forEach(function (item) {
        grid.appendChild(buildCard(item));
      });
      grid.hidden = false;
    } catch (error) {
      $("history-loading").hidden = true;
      $("history-empty").hidden = false;
      toast(error.message, true);
    }
  }

  /* ---------- 6. auth pages ---------------------------------------- */

  function showAuthError(message) {
    var el = $("auth-error");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
  }

  function initAuth() {
    var loginButton = $("login-submit");
    if (loginButton) {
      loginButton.addEventListener("click", async function () {
        loginButton.disabled = true;
        try {
          var result = await api("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: $("email").value, password: $("password").value })
          });
          setToken(result.access_token);
          window.location.href = "/";
        } catch (error) {
          showAuthError(error.message);
          loginButton.disabled = false;
        }
      });
    }

    var signupButton = $("signup-submit");
    if (signupButton) {
      signupButton.addEventListener("click", async function () {
        signupButton.disabled = true;
        try {
          var result = await api("/api/auth/signup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              email: $("email").value,
              password: $("password").value,
              full_name: $("full-name").value
            })
          });
          if (result.access_token) {
            setToken(result.access_token);
            window.location.href = "/";
          } else {
            showAuthError("Account created. Check your email to confirm it, then log in.");
            signupButton.disabled = false;
          }
        } catch (error) {
          showAuthError(error.message);
          signupButton.disabled = false;
        }
      });
    }

    var logoutButton = $("logout-button");
    if (logoutButton) {
      logoutButton.addEventListener("click", function () {
        setToken(null);
        window.location.href = "/";
      });
    }

    // Swap the header buttons depending on whether we hold a token.
    if (CONFIG.authEnabled) {
      var signedIn = !!getToken();
      document.querySelectorAll("[data-auth='signed-in']").forEach(function (el) {
        el.hidden = !signedIn;
      });
      document.querySelectorAll("[data-auth='signed-out']").forEach(function (el) {
        el.hidden = signedIn;
      });
    }
  }

  /* ---------- start ------------------------------------------------ */

  document.addEventListener("DOMContentLoaded", function () {
    initGenerator();
    initHistory();
    initAuth();
  });
})();
