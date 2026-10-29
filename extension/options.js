const DEFAULTS = { appUrl: "http://127.0.0.1:5678", deviceToken: "" };

async function restore() {
  const s = await chrome.storage.local.get(DEFAULTS);
  document.getElementById("appUrl").value = s.appUrl;
  document.getElementById("deviceToken").value = s.deviceToken;
}

document.getElementById("save").addEventListener("click", async () => {
  const appUrl = document.getElementById("appUrl").value.trim() || DEFAULTS.appUrl;
  const deviceToken = document.getElementById("deviceToken").value.trim();
  await chrome.storage.local.set({ appUrl, deviceToken });
  document.getElementById("status").textContent = "Saved.";
});

restore();