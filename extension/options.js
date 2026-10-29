document.getElementById("pair").onclick = async () => {
  const button = document.getElementById("pair"), status = document.getElementById("status");
  button.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ type: "pair", code: document.getElementById("code").value.trim() });
    if (result.error) throw new Error(result.error);
    document.getElementById("code").value = "";
    status.textContent = "Подключено. Войдите в Telegram Web A в созданной вкладке.";
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
};
setInterval(async () => {
  const { status } = await chrome.storage.local.get("status");
  if (status) document.getElementById("status").textContent = status;
}, 3000);
