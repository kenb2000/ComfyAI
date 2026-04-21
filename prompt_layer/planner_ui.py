"""Small browser UI for the Linux-first local planner."""
from __future__ import annotations


def get_planner_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ComfyAI Local Planner</title>
  <style>
    :root {
      --bg: #f1ede6;
      --panel: #fffaf4;
      --border: #d1c4b1;
      --text: #22211d;
      --muted: #6a6359;
      --accent: #235347;
      --accent-soft: #d8ebe5;
      --ok: #256548;
      --warn: #92671f;
      --fail: #943246;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(35, 83, 71, 0.08), transparent 28%),
        linear-gradient(180deg, #ebe3d4 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }
    h1, h2 {
      margin: 0 0 10px;
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    p { margin: 0 0 14px; }
    .grid {
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      align-items: start;
    }
    .panel {
      background: rgba(255, 250, 244, 0.92);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 10px 28px rgba(55, 42, 22, 0.08);
      padding: 18px;
    }
    .stack { display: grid; gap: 12px; }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .muted {
      color: var(--muted);
      font-size: 0.93rem;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 11px;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.03em;
    }
    .badge.ok { background: rgba(37, 101, 72, 0.12); color: var(--ok); }
    .badge.warn { background: rgba(146, 103, 31, 0.12); color: var(--warn); }
    .badge.fail { background: rgba(148, 50, 70, 0.12); color: var(--fail); }
    label { display: grid; gap: 6px; font-size: 0.96rem; }
    input, select, textarea, button {
      font: inherit;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
      color: var(--text);
      padding: 10px 12px;
    }
    textarea {
      min-height: 170px;
      resize: vertical;
    }
    button {
      border: 1px solid var(--accent);
      border-radius: 999px;
      background: var(--accent);
      color: #fffaf4;
      padding: 10px 16px;
      cursor: pointer;
    }
    button.secondary {
      background: var(--accent-soft);
      color: var(--accent);
    }
    pre {
      margin: 0;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
      padding: 12px;
      white-space: pre-wrap;
      overflow-x: auto;
      max-height: 380px;
    }
    ul { margin: 0; padding-left: 18px; }
  </style>
</head>
<body>
  <main>
    <section class="panel stack">
      <h1>ComfyAI Local Planner</h1>
      <p>Linux-first workflow planning now runs inside this repo. Falcon 10B 1.58 is the default local planner/helper baseline so routine workflow preparation no longer depends on the main assistant service.</p>
      <div class="row">
        <span id="plannerBadge" class="badge warn">Planner unknown</span>
        <span id="comfyBadge" class="badge warn">ComfyUI unknown</span>
        <span id="linuxBadge" class="badge warn">Linux workstation unknown</span>
        <span id="summaryLine" class="muted"></span>
      </div>
    </section>

    <div class="grid" style="margin-top: 16px;">
      <section class="panel stack">
        <h2>Local Planner: Falcon 10B 1.58</h2>
        <div class="row">
          <button id="refreshPlannerBtn" type="button" class="secondary">Refresh</button>
          <button id="verifyPlannerBtn" type="button">Verify Planner</button>
          <button id="rebuildPlannerBtn" type="button" class="secondary">Rebuild Planner Runtime</button>
        </div>
        <div id="plannerSummary" class="muted">Loading planner status...</div>
        <ul id="plannerFacts">
          <li>Loading planner facts...</li>
        </ul>
        <pre id="plannerRaw">Loading planner payload...</pre>
      </section>

      <section class="panel stack">
        <h2>Linux Workstation</h2>
        <div class="row">
          <button id="refreshLinuxBtn" type="button" class="secondary">Refresh</button>
          <button id="benchmarkLinuxBtn" type="button">Run Benchmark</button>
        </div>
        <div id="linuxSummary" class="muted">Loading Linux workstation policy...</div>
        <ul id="linuxFacts">
          <li>Loading Linux workstation details...</li>
        </ul>
        <pre id="linuxBenchmark">No benchmark captured yet.</pre>
      </section>
    </div>

    <div class="grid" style="margin-top: 16px;">
      <section class="panel stack">
        <h2>Make Workflow</h2>
        <label>
          Prompt
          <textarea id="promptInput" placeholder="Describe the workflow you want to generate."></textarea>
        </label>
        <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
          <label>
            Workflow profile
            <select id="workflowProfileSelect">
              <option value="preview">preview</option>
              <option value="quality">quality</option>
              <option value="first_last_frame">first_last_frame</option>
              <option value="blender_guided">blender_guided</option>
            </select>
          </label>
          <label>
            Width
            <input id="widthInput" type="number" min="64" step="64" placeholder="1024">
          </label>
          <label>
            Height
            <input id="heightInput" type="number" min="64" step="64" placeholder="1024">
          </label>
        </div>
        <label>
          <input id="queueToggle" type="checkbox">
          Queue workflow in ComfyUI after validation
        </label>
        <label>
          <input id="lowImpactToggle" type="checkbox" checked>
          Low workstation impact mode
        </label>
        <div class="row">
          <button id="runPlannerBtn" type="button">Generate Plan</button>
          <button id="refreshWorkflowsBtn" type="button" class="secondary">Refresh Saved Workflows</button>
        </div>
        <pre id="plannerEvents">Planner events will appear here.</pre>
      </section>

      <section class="panel stack">
        <h2>Saved Workflows</h2>
        <div id="workflowSummary" class="muted">Loading generated workflows...</div>
        <ul id="workflowList">
          <li>No saved workflows yet.</li>
        </ul>
      </section>
    </div>
  </main>

  <script>
    const plannerBadge = document.getElementById("plannerBadge");
    const comfyBadge = document.getElementById("comfyBadge");
    const linuxBadge = document.getElementById("linuxBadge");
    const summaryLine = document.getElementById("summaryLine");
    const plannerSummary = document.getElementById("plannerSummary");
    const plannerFacts = document.getElementById("plannerFacts");
    const plannerRaw = document.getElementById("plannerRaw");
    const linuxSummary = document.getElementById("linuxSummary");
    const linuxFacts = document.getElementById("linuxFacts");
    const linuxBenchmark = document.getElementById("linuxBenchmark");
    const plannerEvents = document.getElementById("plannerEvents");
    const workflowSummary = document.getElementById("workflowSummary");
    const workflowList = document.getElementById("workflowList");

    function badgeClass(ok, warnText) {
      if (ok === true) return "badge ok";
      if (ok === false) return "badge fail";
      return "badge warn";
    }

    function setBadge(node, text, ok) {
      node.textContent = text;
      node.className = badgeClass(ok, text);
    }

    async function getJson(url, options) {
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || response.statusText);
      }
      return payload;
    }

    function setList(node, items) {
      node.innerHTML = "";
      if (!items.length) {
        const li = document.createElement("li");
        li.textContent = "None";
        node.appendChild(li);
        return;
      }
      for (const item of items) {
        const li = document.createElement("li");
        li.textContent = item;
        node.appendChild(li);
      }
    }

    function renderPlanner(planner) {
      const ready = Boolean(planner.ready);
      const lastVerify = planner.last_verify_at || "never";
      setBadge(plannerBadge, ready ? "Planner ready" : `Planner ${planner.status || "unknown"}`, ready ? true : planner.model_present ? false : null);
      plannerSummary.textContent = `mode=${planner.mode || "local"} | status=${planner.status || "unknown"} | last verify=${lastVerify}`;
      setList(plannerFacts, [
        `model id: ${planner.default_model_id || "unknown"}`,
        `model path: ${planner.model_path || "unresolved"}`,
        `platform target: ${planner.platform_target || "linux"}`,
        `repairs before fail: ${planner.max_repairs_before_fail ?? "unknown"}`,
        `request timeout seconds: ${planner.request_timeout_seconds ?? "unknown"}`,
        `planner output dir: ${planner.planner_output_dir || "unknown"}`,
      ]);
      plannerRaw.textContent = JSON.stringify(planner, null, 2);
    }

    function renderComfy(comfy) {
      const ok = Boolean(comfy.health_ok) && Boolean(comfy.object_info_probe && comfy.object_info_probe.ok);
      setBadge(comfyBadge, ok ? "ComfyUI ready" : "ComfyUI verify needed", ok);
    }

    function renderLinux(linux) {
      const ready = Boolean(linux.capabilities && linux.capabilities.ltx_video_node_available);
      setBadge(linuxBadge, ready ? "Linux workstation ready" : "Linux workstation partial", ready ? true : null);
      linuxSummary.textContent = `${linux.machine_label || "Linux workstation"} | profile=${linux.active_profile || "unknown"}`;
      setList(linuxFacts, [
        `role: ${linux.role_label || linux.role || "unknown"}`,
        `recommended profile: ${(linux.recommended_config || {}).workflow_profile || "unknown"}`,
        `async offload: ${String(((linux.recommended_config || {}).optimizations || {}).async_offload)}`,
        `pinned memory: ${String(((linux.recommended_config || {}).optimizations || {}).pinned_memory)}`,
        `weight streaming: ${String(((linux.recommended_config || {}).optimizations || {}).weight_streaming)}`,
      ]);
    }

    function renderWorkflows(payload) {
      workflowSummary.textContent = `${payload.count || 0} saved workflow${payload.count === 1 ? "" : "s"}`;
      const items = (payload.items || []).map((item) => `${item.name} | ${item.relative_path}`);
      setList(workflowList, items);
    }

    async function refreshStatus() {
      const status = await getJson("/setup/status");
      renderPlanner(status.planner || {});
      renderComfy(status.comfyui || {});
      renderLinux(status.linux_workstation || {});
      summaryLine.textContent = `planner=${(status.planner || {}).status || "unknown"} | comfy=${(status.comfyui || {}).health_ok ? "healthy" : "not ready"} | generated workflows dir=${(status.workspace || {}).generated_workflows_dir || "unknown"}`;
      const workflows = await getJson("/workspace/workflows");
      renderWorkflows(workflows);
    }

    async function verifyPlanner() {
      plannerEvents.textContent = "Verifying local planner...";
      try {
        const payload = await getJson("/planner/verify", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({}),
        });
        plannerEvents.textContent = JSON.stringify(payload, null, 2);
        await refreshStatus();
      } catch (error) {
        plannerEvents.textContent = String(error);
      }
    }

    async function rebuildPlanner() {
      plannerEvents.textContent = "Rebuilding planner runtime...";
      try {
        const payload = await getJson("/planner/rebuild", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({}),
        });
        plannerEvents.textContent = JSON.stringify(payload, null, 2);
        await refreshStatus();
      } catch (error) {
        plannerEvents.textContent = String(error);
      }
    }

    async function runBenchmark() {
      linuxBenchmark.textContent = "Running Linux benchmark...";
      try {
        const payload = await getJson("/setup/benchmark", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({}),
        });
        linuxBenchmark.textContent = JSON.stringify(payload, null, 2);
        await refreshStatus();
      } catch (error) {
        linuxBenchmark.textContent = String(error);
      }
    }

    async function runPlanner() {
      plannerEvents.textContent = "Generating local plan...";
      const payload = {
        prompt: document.getElementById("promptInput").value,
        workflow_profile: document.getElementById("workflowProfileSelect").value,
        low_workstation_impact: document.getElementById("lowImpactToggle").checked,
        queue_workflow: document.getElementById("queueToggle").checked,
      };
      const width = document.getElementById("widthInput").value;
      const height = document.getElementById("heightInput").value;
      if (width) payload.width = Number(width);
      if (height) payload.height = Number(height);
      try {
        const response = await fetch("/helper/process", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        const text = await response.text();
        const lines = text.split("\\n").filter((line) => line.trim()).map((line) => {
          try { return JSON.parse(line); } catch (_) { return {raw: line}; }
        });
        plannerEvents.textContent = JSON.stringify(lines, null, 2);
        await refreshStatus();
      } catch (error) {
        plannerEvents.textContent = String(error);
      }
    }

    document.getElementById("refreshPlannerBtn").addEventListener("click", refreshStatus);
    document.getElementById("verifyPlannerBtn").addEventListener("click", verifyPlanner);
    document.getElementById("rebuildPlannerBtn").addEventListener("click", rebuildPlanner);
    document.getElementById("refreshLinuxBtn").addEventListener("click", refreshStatus);
    document.getElementById("benchmarkLinuxBtn").addEventListener("click", runBenchmark);
    document.getElementById("runPlannerBtn").addEventListener("click", runPlanner);
    document.getElementById("refreshWorkflowsBtn").addEventListener("click", refreshStatus);

    refreshStatus().catch((error) => {
      summaryLine.textContent = String(error);
    });
  </script>
</body>
</html>
"""
