"""Small browser UI for the shared planner/helper bridge."""
from __future__ import annotations


def get_planner_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ComfyUIhybrid Planner Bridge</title>
  <style>
    :root {
      --bg: #f3efe4;
      --panel: #fffaf0;
      --border: #d4c4a8;
      --text: #1f241f;
      --accent: #6d4c2f;
      --accent-soft: #f0ddbe;
      --ok: #2a6b48;
      --warn: #8b5e1a;
      --fail: #8c2f39;
    }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background: linear-gradient(180deg, #efe7d5 0%, var(--bg) 100%);
      color: var(--text);
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }
    h1, h2 {
      margin: 0 0 12px;
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    p {
      margin: 0 0 16px;
    }
    .grid {
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: 0 8px 24px rgba(83, 58, 29, 0.08);
      padding: 18px;
    }
    .stack {
      display: grid;
      gap: 12px;
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 0.95rem;
    }
    input, select, textarea, button {
      font: inherit;
    }
    input, select, textarea {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      background: #fff;
      color: var(--text);
    }
    textarea {
      min-height: 150px;
      resize: vertical;
    }
    button {
      border: 1px solid var(--accent);
      border-radius: 999px;
      padding: 10px 16px;
      background: var(--accent);
      color: #fffaf0;
      cursor: pointer;
    }
    button.secondary {
      background: var(--accent-soft);
      color: var(--accent);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .muted {
      color: #62563f;
      font-size: 0.92rem;
    }
    .badge {
      display: inline-block;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.82rem;
      font-weight: 600;
      letter-spacing: 0.03em;
    }
    .badge.ok {
      background: rgba(42, 107, 72, 0.12);
      color: var(--ok);
    }
    .badge.warn {
      background: rgba(139, 94, 26, 0.12);
      color: var(--warn);
    }
    .badge.fail {
      background: rgba(140, 47, 57, 0.12);
      color: var(--fail);
    }
    pre {
      margin: 0;
      padding: 12px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #fff;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 360px;
    }
    ul {
      margin: 0;
      padding-left: 18px;
    }
    .hidden {
      display: none;
    }
  </style>
</head>
<body>
  <main>
    <div class="panel stack">
      <h1>ComfyUIhybrid Planner Bridge</h1>
      <p>Use the shared planner/helper service instead of duplicating planner logic locally. Policy lives on the main assistant backend; ComfyUIhybrid stores generated workflow configs locally for reuse.</p>
      <div class="row">
        <span id="plannerStatusBadge" class="badge warn">Planner unknown</span>
        <span id="comfyStatusBadge" class="badge warn">ComfyUI unknown</span>
        <span class="muted" id="statusSummary"></span>
      </div>
    </div>

    <div class="grid" style="margin-top: 16px;">
      <section class="panel stack">
        <h2>Planner Service</h2>
        <label>
          Main assistant repo path
          <input id="assistantRepoPath" type="text" placeholder="Path to the main assistant backend repo">
        </label>
        <div class="row">
          <span id="plannerServiceBadge" class="badge warn">Service unknown</span>
          <button id="savePlannerServiceBtn" type="button" class="secondary">Save Repo Path</button>
          <button id="startPlannerServiceBtn" type="button">Start Planner Service</button>
          <button id="stopPlannerServiceBtn" type="button" class="secondary">Stop Planner Service</button>
          <button id="refreshPlannerServiceBtn" type="button" class="secondary">Refresh Service</button>
        </div>
        <div class="muted" id="plannerServiceSummary"></div>
        <pre id="plannerServiceRaw">Loading planner service status...</pre>
      </section>

      <section class="panel stack">
        <h2>Planner Policy</h2>
        <label>
          Model mode selector
          <select id="modeSelect">
            <option value="manual">Manual</option>
            <option value="auto">Auto</option>
            <option value="research">Research</option>
          </select>
        </label>

        <div id="manualSection" class="stack">
          <label>
            Manual model
            <select id="manualModelSelect"></select>
          </label>
          <div class="muted">Manual mode uses the model list returned by <code>/planner/models</code>.</div>
        </div>

        <div id="autoSection" class="stack hidden">
          <div class="muted" id="autoModeSummary">Auto mode defers model selection to the shared planner policy.</div>
          <div class="muted" id="autoBestLadderStatus">No cached best ladder is available yet.</div>
          <ul id="autoBestLadderDetails">
            <li>No best ladder summary yet.</li>
          </ul>
        </div>

        <div id="researchSection" class="stack hidden">
          <label>
            Passes
            <input id="researchPasses" type="number" min="1" step="1" value="2">
          </label>
          <label>
            Timeout seconds
            <input id="researchTimeout" type="number" min="1" step="1" value="90">
          </label>
          <label>
            Fallback model
            <select id="researchFallbackSelect"></select>
          </label>
          <div class="row">
            <button id="runResearchBtn" type="button">Run Research</button>
          </div>
          <div class="muted" id="researchBestLadderStatus">No best ladder has been mirrored locally yet.</div>
          <ul id="researchBestLadderDetails">
            <li>No best ladder summary yet.</li>
          </ul>
          <pre id="researchStatus">Research status will appear here.</pre>
        </div>

        <div class="row">
          <button id="savePolicyBtn" type="button">Save Policy</button>
          <button id="refreshPolicyBtn" type="button" class="secondary">Refresh</button>
        </div>
        <pre id="policyRaw">Loading policy...</pre>
      </section>

      <section class="panel stack">
        <h2>Make Workflow</h2>
        <label>
          Request
          <textarea id="workflowPrompt" placeholder="Describe the workflow you want the planner to produce."></textarea>
        </label>
        <div class="row">
          <button id="runWorkflowBtn" type="button">Make Workflow</button>
          <button id="clearEventsBtn" type="button" class="secondary">Clear Events</button>
        </div>
        <div class="muted">The event log shows streamed <code>tool_call</code> and <code>tool_result</code> events from the shared helper pipeline.</div>
        <pre id="eventLog">No events yet.</pre>
      </section>
    </div>

    <section class="panel stack" style="margin-top: 16px;">
      <h2>Saved Workflow Configs</h2>
      <div class="muted">Generated workflow configs are stored in the local workspace area so ComfyUIhybrid can reuse them later without copying planner logic into this repo.</div>
      <ul id="savedWorkflows">
        <li>No saved workflows yet.</li>
      </ul>
    </section>
  </main>

  <script>
    const state = {
      models: [],
      policy: {},
      plannerService: {},
      bestLadderCache: {},
    };

    function setBadge(element, ok, warnText, okText, failText) {
      element.className = "badge " + (ok === true ? "ok" : (ok === false ? "fail" : "warn"));
      element.textContent = ok === true ? okText : (ok === false ? failText : warnText);
    }

    function pretty(value) {
      return JSON.stringify(value, null, 2);
    }

    function appendEvent(value) {
      const eventLog = document.getElementById("eventLog");
      const prefix = eventLog.textContent === "No events yet." ? "" : eventLog.textContent + "\\n";
      eventLog.textContent = prefix + pretty(value);
      eventLog.scrollTop = eventLog.scrollHeight;
    }

    function normalizeModels(payload) {
      if (Array.isArray(payload)) {
        return payload;
      }
      if (Array.isArray(payload.models)) {
        return payload.models;
      }
      if (Array.isArray(payload.items)) {
        return payload.items;
      }
      return [];
    }

    function modelName(item) {
      if (typeof item === "string") {
        return item;
      }
      if (item && typeof item === "object") {
        return item.id || item.model || item.name || JSON.stringify(item);
      }
      return String(item);
    }

    function formatTimestamp(value) {
      if (!value) {
        return "";
      }
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) {
        return String(value);
      }
      return parsed.toLocaleString();
    }

    function normalizeBestLadderCache(cache) {
      const summary = cache && typeof cache === "object" ? (cache.summary || {}) : {};
      const normalizeList = (value) => {
        if (Array.isArray(value)) {
          return value.map((item) => String(item)).filter(Boolean);
        }
        if (value == null || value === "") {
          return [];
        }
        return [String(value)];
      };
      return {
        available: Boolean(cache && cache.available),
        savedAt: cache ? (cache.saved_at || null) : null,
        displayTimestamp: cache ? (cache.display_timestamp || cache.saved_at || null) : null,
        source: cache ? (cache.source || "planner_policy") : "planner_policy",
        policyMode: cache ? (cache.policy_mode || null) : null,
        headline: summary.headline || "",
        baseline: normalizeList(summary.baseline),
        tierMappings: normalizeList(summary.tier_mappings),
        thresholds: normalizeList(summary.thresholds),
      };
    }

    function renderBestLadderList(elementId, cache) {
      const list = document.getElementById(elementId);
      list.innerHTML = "";
      const lines = [];
      for (const item of cache.baseline) {
        lines.push("Baseline: " + item);
      }
      for (const item of cache.tierMappings) {
        lines.push("Tier: " + item);
      }
      for (const item of cache.thresholds) {
        lines.push("Threshold: " + item);
      }
      if (!lines.length) {
        const li = document.createElement("li");
        li.textContent = "No best ladder summary yet.";
        list.appendChild(li);
        return;
      }
      for (const line of lines) {
        const li = document.createElement("li");
        li.textContent = line;
        list.appendChild(li);
      }
    }

    function applyBestLadderCache(cache) {
      state.bestLadderCache = normalizeBestLadderCache(cache || {});
      const ladder = state.bestLadderCache;
      const autoSummary = document.getElementById("autoModeSummary");
      const autoStatus = document.getElementById("autoBestLadderStatus");
      const researchStatus = document.getElementById("researchBestLadderStatus");

      if (ladder.available) {
        const timestamp = formatTimestamp(ladder.displayTimestamp);
        autoSummary.textContent = "Auto mode defers model selection to the shared planner policy.";
        autoStatus.textContent = "Auto using best ladder from " + timestamp + ".";
        researchStatus.textContent =
          "Best ladder mirrored locally" +
          (timestamp ? (" from " + timestamp) : "") +
          ".";
      } else {
        autoSummary.textContent = "Auto mode defers model selection to the shared planner policy.";
        autoStatus.textContent = "No cached best ladder is available yet.";
        researchStatus.textContent = "No best ladder has been mirrored locally yet.";
      }

      renderBestLadderList("autoBestLadderDetails", ladder);
      renderBestLadderList("researchBestLadderDetails", ladder);
    }

    function buildPolicyPayload() {
      const manualModel = document.getElementById("manualModelSelect").value;
      const fallbackModel = document.getElementById("researchFallbackSelect").value;
      const next = Object.assign({}, state.policy || {});
      next.mode = document.getElementById("modeSelect").value;
      next.manual = Object.assign({}, next.manual || {}, {
        model: manualModel,
      });
      next.research = Object.assign({}, next.research || {}, {
        passes: Number(document.getElementById("researchPasses").value || 2),
        timeout_seconds: Number(document.getElementById("researchTimeout").value || 90),
        fallback_model: fallbackModel,
      });
      return next;
    }

    function applyPolicy(policy) {
      state.policy = policy || {};
      const mode = state.policy.mode || "auto";
      document.getElementById("modeSelect").value = mode;
      const manualModel = (state.policy.manual || {}).model || "";
      if (manualModel) {
        document.getElementById("manualModelSelect").value = manualModel;
      }
      const research = state.policy.research || {};
      if (research.passes != null) {
        document.getElementById("researchPasses").value = research.passes;
      }
      if (research.timeout_seconds != null) {
        document.getElementById("researchTimeout").value = research.timeout_seconds;
      }
      if (research.fallback_model) {
        document.getElementById("researchFallbackSelect").value = research.fallback_model;
      }
      document.getElementById("policyRaw").textContent = pretty(policy);
      toggleModeSections();
      if (policy && policy.auto_best_ladder_cache) {
        applyBestLadderCache(policy.auto_best_ladder_cache);
      }
    }

    function populateModels(models) {
      state.models = normalizeModels(models);
      const manual = document.getElementById("manualModelSelect");
      const fallback = document.getElementById("researchFallbackSelect");
      manual.innerHTML = "";
      fallback.innerHTML = "";
      if (!state.models.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No models available";
        manual.appendChild(option);
        fallback.appendChild(option.cloneNode(true));
        return;
      }
      for (const item of state.models) {
        const name = modelName(item);
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        manual.appendChild(option);
        fallback.appendChild(option.cloneNode(true));
      }
    }

    function toggleModeSections() {
      const mode = document.getElementById("modeSelect").value;
      document.getElementById("manualSection").classList.toggle("hidden", mode !== "manual");
      document.getElementById("autoSection").classList.toggle("hidden", mode !== "auto");
      document.getElementById("researchSection").classList.toggle("hidden", mode !== "research");
    }

    async function fetchJson(path, options) {
      const response = await fetch(path, options);
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || ("Request failed: " + response.status));
      }
      return await response.json();
    }

    function applyPlannerServiceStatus(status) {
      state.plannerService = status || {};
      document.getElementById("assistantRepoPath").value = status.assistant_repo_path || "";

      const healthy = status.healthy === true;
      const unhealthy = status.healthy === false;
      setBadge(
        document.getElementById("plannerStatusBadge"),
        healthy ? true : (unhealthy ? false : null),
        "Planner unknown",
        "Planner healthy",
        "Planner down"
      );
      setBadge(
        document.getElementById("plannerServiceBadge"),
        healthy ? true : (unhealthy ? false : null),
        "Service unknown",
        "Service healthy",
        "Service down"
      );

      document.getElementById("plannerServiceSummary").textContent =
        "Health " + (status.health_url || "") +
        " | repo " + (status.assistant_repo_path || "(not set)") +
        " | can_start=" + String(Boolean(status.can_start)) +
        " | can_stop=" + String(Boolean(status.can_stop));
      document.getElementById("plannerServiceRaw").textContent = pretty(status);
      document.getElementById("statusSummary").textContent =
        "Planner " + (status.base_url || "") + " | ComfyUI " + (document.getElementById("statusSummary").dataset.comfyUrl || "");

      document.getElementById("startPlannerServiceBtn").disabled = !Boolean(status.can_start) || Boolean(status.healthy);
      document.getElementById("stopPlannerServiceBtn").disabled = !Boolean(status.can_stop) && !Boolean(status.healthy);
      document.getElementById("savePolicyBtn").disabled = !Boolean(status.healthy);
      document.getElementById("runResearchBtn").disabled = !Boolean(status.healthy);
      document.getElementById("runWorkflowBtn").disabled = !Boolean(status.healthy);
    }

    async function loadStatus() {
      const data = await fetchJson("/setup/status");
      const comfy = data.comfyui || {};
      const planner = data.planner || {};
      setBadge(
        document.getElementById("comfyStatusBadge"),
        comfy.reachable === true,
        "ComfyUI unknown",
        "ComfyUI reachable",
        "ComfyUI unreachable"
      );
      document.getElementById("statusSummary").dataset.comfyUrl = comfy.base_url || "";
      applyBestLadderCache(planner.auto_best_ladder_cache || {});
      return data;
    }

    async function loadPlannerServiceStatus() {
      const data = await fetchJson("/planner/service/status");
      applyPlannerServiceStatus(data);
      return data;
    }

    async function loadPolicyAndModels() {
      if (!state.plannerService.healthy) {
        populateModels([]);
        document.getElementById("policyRaw").textContent = "Planner service is not running.";
        document.getElementById("researchStatus").textContent = "Planner service is not running.";
        return;
      }
      try {
        const [models, policy] = await Promise.all([
          fetchJson("/planner/models"),
          fetchJson("/planner/policy"),
        ]);
        populateModels(models);
        applyPolicy(policy);
        await loadStatus();
      } catch (error) {
        document.getElementById("policyRaw").textContent = String(error);
      }
    }

    async function loadSavedWorkflows() {
      const data = await fetchJson("/workspace/workflows");
      const list = document.getElementById("savedWorkflows");
      list.innerHTML = "";
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        const li = document.createElement("li");
        li.textContent = "No saved workflows yet.";
        list.appendChild(li);
        return;
      }
      for (const item of items) {
        const li = document.createElement("li");
        li.textContent = item.name + " | " + item.relative_path + " | " + item.modified_at;
        list.appendChild(li);
      }
    }

    async function savePlannerServiceConfig() {
      const payload = {
        assistant_repo_path: document.getElementById("assistantRepoPath").value.trim(),
      };
      const data = await fetchJson("/planner/service/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      applyPlannerServiceStatus(data);
      return data;
    }

    async function startPlannerService() {
      document.getElementById("plannerServiceRaw").textContent = "Starting planner service...";
      const payload = {
        assistant_repo_path: document.getElementById("assistantRepoPath").value.trim(),
      };
      try {
        const data = await fetchJson("/planner/service/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        applyPlannerServiceStatus(data.status || {});
        await loadPolicyAndModels();
      } catch (error) {
        document.getElementById("plannerServiceRaw").textContent = String(error);
      }
    }

    async function stopPlannerService() {
      document.getElementById("plannerServiceRaw").textContent = "Stopping planner service...";
      try {
        const data = await fetchJson("/planner/service/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        applyPlannerServiceStatus(data.status || {});
        await loadPolicyAndModels();
      } catch (error) {
        document.getElementById("plannerServiceRaw").textContent = String(error);
      }
    }

    async function savePolicy() {
      if (!state.plannerService.healthy) {
        document.getElementById("policyRaw").textContent = "Planner service is not running.";
        return;
      }
      const payload = buildPolicyPayload();
      const data = await fetchJson("/planner/policy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      applyPolicy(data);
    }

    async function runResearch() {
      if (!state.plannerService.healthy) {
        document.getElementById("researchStatus").textContent = "Planner service is not running.";
        return;
      }
      const payload = buildPolicyPayload();
      payload.mode = "research";
      const status = document.getElementById("researchStatus");
      status.textContent = "Running research...";
      try {
        const data = await fetchJson("/planner/research/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        status.textContent = pretty(data);
        if (data && data.auto_best_ladder_cache) {
          applyBestLadderCache(data.auto_best_ladder_cache);
        }
        await loadPolicyAndModels();
      } catch (error) {
        status.textContent = String(error);
      }
    }

    async function runWorkflow() {
      const prompt = document.getElementById("workflowPrompt").value.trim();
      if (!prompt) {
        appendEvent({ event: "error", message: "Enter a workflow request first." });
        return;
      }
      if (!state.plannerService.healthy) {
        appendEvent({ event: "error", message: "Planner service is not running." });
        return;
      }
      document.getElementById("eventLog").textContent = "Streaming events...";
      const payload = {
        action: "make_workflow",
        prompt: prompt,
        request: prompt,
        planner_policy: buildPolicyPayload(),
        mode: document.getElementById("modeSelect").value,
      };
      const response = await fetch("/helper/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok || !response.body) {
        const detail = await response.text();
        appendEvent({ event: "error", message: detail || ("Request failed: " + response.status) });
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";
      for await (const chunk of response.body) {
        buffer += decoder.decode(chunk, { stream: true });
        const lines = buffer.split("\\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          const text = line.trim();
          if (!text) {
            continue;
          }
          try {
            const event = JSON.parse(text);
            appendEvent(event);
            if (event.event === "workflow_saved") {
              await loadSavedWorkflows();
            }
          } catch (error) {
            appendEvent({ event: "text", data: text });
          }
        }
      }
      if (buffer.trim()) {
        try {
          appendEvent(JSON.parse(buffer.trim()));
        } catch (error) {
          appendEvent({ event: "text", data: buffer.trim() });
        }
      }
    }

    async function refreshAll() {
      await loadStatus();
      await loadPlannerServiceStatus();
      await loadPolicyAndModels();
      await loadSavedWorkflows();
    }

    document.getElementById("modeSelect").addEventListener("change", toggleModeSections);
    document.getElementById("savePlannerServiceBtn").addEventListener("click", savePlannerServiceConfig);
    document.getElementById("startPlannerServiceBtn").addEventListener("click", startPlannerService);
    document.getElementById("stopPlannerServiceBtn").addEventListener("click", stopPlannerService);
    document.getElementById("refreshPlannerServiceBtn").addEventListener("click", refreshAll);
    document.getElementById("savePolicyBtn").addEventListener("click", savePolicy);
    document.getElementById("refreshPolicyBtn").addEventListener("click", loadPolicyAndModels);
    document.getElementById("runResearchBtn").addEventListener("click", runResearch);
    document.getElementById("runWorkflowBtn").addEventListener("click", runWorkflow);
    document.getElementById("clearEventsBtn").addEventListener("click", () => {
      document.getElementById("eventLog").textContent = "No events yet.";
    });

    refreshAll().catch((error) => {
      document.getElementById("eventLog").textContent = String(error);
    });
  </script>
</body>
</html>
"""
