const invoke = window.__TAURI__?.core?.invoke;

const statusEl = document.getElementById('status');
const detailsEl = document.getElementById('details');
const roleTitleEl = document.getElementById('roleTitle');
const roleSummaryEl = document.getElementById('roleSummary');
const capabilityListEl = document.getElementById('capabilityList');
const recommendationEl = document.getElementById('recommendation');
const verifyOutputEl = document.getElementById('verifyOutput');
const benchmarkOutputEl = document.getElementById('benchmarkOutput');
const launchButton = document.getElementById('launch');
const openEditorButton = document.getElementById('openEditor');
const verifyButton = document.getElementById('verify');
const benchmarkButton = document.getElementById('benchmark');
const refreshButton = document.getElementById('refresh');

const state = {
  comfyUrl: '',
  status: null,
  verify: null,
  benchmark: null,
};

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setDetails(text) {
  detailsEl.textContent = text;
}

function setBusy(button, pending) {
  button.disabled = pending;
}

function setError(message) {
  setStatus('Workstation action failed.');
  setDetails(String(message));
}

function renderCapabilityList(linux) {
  const capabilities = (linux && linux.capabilities) || {};
  const optimizations = (linux && linux.optimizations) || {};
  const lines = [
    `NVIDIA GPU present: ${Boolean(capabilities.nvidia_gpu_present)}`,
    `LTXVideo nodes available: ${Boolean(capabilities.ltx_video_node_available)}`,
    `FP8 checkpoints available: ${Boolean(capabilities.fp8_capable_checkpoints_available)}`,
    `LTX-2.3 checkpoints available: ${Boolean(capabilities.ltx_23_checkpoints_available)}`,
    `Async offload: available=${Boolean((optimizations.async_offload || {}).available)} enabled=${Boolean((optimizations.async_offload || {}).enabled)}`,
    `Pinned memory: available=${Boolean((optimizations.pinned_memory || {}).available)} enabled=${Boolean((optimizations.pinned_memory || {}).enabled)}`,
    `Weight streaming: available=${Boolean((optimizations.weight_streaming || {}).available)} enabled=${Boolean((optimizations.weight_streaming || {}).enabled)}`,
    `Blender present: ${Boolean(capabilities.blender_present)}`,
    `NVFP4 supported: ${Boolean(capabilities.nvfp4_supported)}`,
  ];
  capabilityListEl.innerHTML = '';
  for (const line of lines) {
    const item = document.createElement('li');
    item.textContent = line;
    capabilityListEl.appendChild(item);
  }
}

function renderStatus(data) {
  state.status = data;
  const linux = (data || {}).linux_workstation || {};
  const comfy = (data || {}).comfyui || {};
  const latestBenchmark = linux.latest_benchmark || null;
  const latestVerification = linux.latest_verification || null;
  const recommendation = latestBenchmark?.recommended_config || latestVerification?.recommended_config || null;

  roleTitleEl.textContent = linux.role_label || 'Linux workstation';
  roleSummaryEl.textContent = `${linux.machine_label || 'Stable local generation and fallback node'} | active profile ${linux.active_profile || 'linux_stable_nvidia'}`;
  renderCapabilityList(linux);
  recommendationEl.textContent = pretty({
    role: linux.role_label,
    active_profile: linux.active_profile,
    recommended_config: recommendation,
    comfyui_url: comfy.base_url,
  });

  if (latestBenchmark) {
    benchmarkOutputEl.textContent = pretty(latestBenchmark);
  }
  if (latestVerification) {
    verifyOutputEl.textContent = pretty(latestVerification);
  }

  state.comfyUrl = comfy.base_url || state.comfyUrl;
  openEditorButton.disabled = !(comfy.reachable === true && state.comfyUrl);
  setStatus(
    comfy.reachable === true && state.comfyUrl
      ? `Linux workstation ready. ComfyUI endpoint: ${state.comfyUrl}`
      : 'Linux workstation policy loaded. Launch ComfyUI when you want the local editor running.'
  );
  setDetails(pretty({
    comfyui: comfy,
    planner: (data || {}).planner || {},
    linux_workstation: linux,
  }));
}

async function refreshStatus() {
  if (!invoke) {
    setError('Tauri IPC is not available in the shell dashboard.');
    return;
  }
  setBusy(refreshButton, true);
  setStatus('Refreshing Linux workstation status.');
  try {
    const data = await invoke('get_workstation_status');
    renderStatus(data);
  } catch (error) {
    setError(error);
  } finally {
    setBusy(refreshButton, false);
  }
}

async function launchComfyUI() {
  if (!invoke) {
    setError('Tauri IPC is not available in the shell dashboard.');
    return;
  }
  setBusy(launchButton, true);
  setStatus('Launching the local ComfyUI runtime.');
  setDetails('Starting the Linux workstation sidecar and waiting for the configured health endpoint.');
  try {
    const result = await invoke('launch_comfyui_shell');
    state.comfyUrl = result.url;
    openEditorButton.disabled = false;
    setStatus(result.reusedExisting ? 'Attached to the existing ComfyUI sidecar.' : 'ComfyUI sidecar launched successfully.');
    setDetails(`Editor ready at ${result.url}`);
    await refreshStatus();
  } catch (error) {
    setError(error);
  } finally {
    setBusy(launchButton, false);
  }
}

async function verifyWorkstation() {
  if (!invoke) {
    setError('Tauri IPC is not available in the shell dashboard.');
    return;
  }
  setBusy(verifyButton, true);
  verifyOutputEl.textContent = 'Running verify...';
  try {
    const data = await invoke('verify_workstation');
    state.verify = data;
    verifyOutputEl.textContent = pretty(data);
    await refreshStatus();
  } catch (error) {
    verifyOutputEl.textContent = String(error);
    setError(error);
  } finally {
    setBusy(verifyButton, false);
  }
}

async function benchmarkWorkstation() {
  if (!invoke) {
    setError('Tauri IPC is not available in the shell dashboard.');
    return;
  }
  setBusy(benchmarkButton, true);
  benchmarkOutputEl.textContent = 'Running benchmark...';
  try {
    const data = await invoke('benchmark_workstation');
    state.benchmark = data;
    benchmarkOutputEl.textContent = pretty(data);
    await refreshStatus();
  } catch (error) {
    benchmarkOutputEl.textContent = String(error);
    setError(error);
  } finally {
    setBusy(benchmarkButton, false);
  }
}

launchButton.addEventListener('click', launchComfyUI);
openEditorButton.addEventListener('click', () => {
  if (state.comfyUrl) {
    window.location.replace(state.comfyUrl);
  }
});
verifyButton.addEventListener('click', verifyWorkstation);
benchmarkButton.addEventListener('click', benchmarkWorkstation);
refreshButton.addEventListener('click', refreshStatus);

refreshStatus();
