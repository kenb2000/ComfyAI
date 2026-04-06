const invoke = window.__TAURI__?.core?.invoke;

const statusEl = document.getElementById('status');
const detailsEl = document.getElementById('details');
const retryButton = document.getElementById('retry');
const panel = document.querySelector('.panel');

function setStatus(text) {
  statusEl.textContent = text;
}

function setDetails(text) {
  detailsEl.textContent = text;
}

function setError(message) {
  panel.classList.add('error');
  retryButton.classList.remove('hidden');
  setStatus('ComfyUI did not finish starting.');
  setDetails(String(message));
}

async function startShell() {
  retryButton.classList.add('hidden');
  panel.classList.remove('error');

  if (!invoke) {
    setError('Tauri IPC is not available in the shell bootstrap page.');
    return;
  }

  setStatus('Launching the local ComfyUI runtime. This can take a moment on the first start.');
  setDetails('Spawning scripts/launch_comfyui.py and waiting for the configured health endpoint.');

  try {
    const result = await invoke('launch_comfyui_shell');
    setStatus(result.reusedExisting ? 'Attached to an existing ComfyUI server.' : 'ComfyUI is ready. Opening the editor.');
    setDetails(`Opening ${result.url}`);
    window.location.replace(result.url);
  } catch (error) {
    setError(error);
  }
}

window.__COMFYAI_SET_STATUS = (status, details) => {
  panel.classList.remove('error');
  retryButton.classList.add('hidden');
  setStatus(status);
  if (details) {
    setDetails(details);
  }
};

window.__COMFYAI_SET_ERROR = (message) => {
  setError(message);
};

window.__COMFYAI_OPEN_URL = (url) => {
  setStatus('ComfyUI is ready. Opening the editor.');
  setDetails(`Opening ${url}`);
  window.location.replace(url);
};

retryButton.addEventListener('click', () => {
  startShell();
});
