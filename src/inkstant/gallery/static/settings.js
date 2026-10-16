// The settings page (SPEC.md §11.4.6, §11.4.7). Toggles, selects and switches save as they change.
import { $, $$, api, el, onStatus, openDialog, pollNow, setPhoneTime, sharingEnded, startPolling, toast } from "./common.js";
import { S } from "./strings.js";

let settings = null;
let lastStatus = null;

const OPTIONS = {
  "slideshow.interval_min": [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440],
  sleep_min: [1, 2, 3, 5, 10, 15, null],
  auto_off_min: [1, 2, 5, 10, 15, 20, 30, 45, 60],
  share_timeout_min: [5, 10, 15, 20, 30, 45, 60],
  low_battery_shutdown_pct: [4, 5, 8, 10, 15, 20],
};

function label(key, v) {
  if (v === null) return S.never;
  if (key === "slideshow.interval_min") return v >= 60 ? S.hours(v / 60) : S.min(v);
  if (key === "low_battery_shutdown_pct") return S.pct(v);
  return S.min(v);
}

function get(obj, key) {
  return key.split(".").reduce((o, k) => (o ? o[k] : undefined), obj);
}

function partial(key, value) {
  const [a, b] = key.split(".");
  return b ? { [a]: { [b]: value } } : { [a]: value };
}

function flash(control) {
  const host = control.closest(".row2") || control.parentElement;
  host.querySelector(".saved")?.remove();
  const tick = el("span", { class: "saved", text: `✓ ${S.saved}` });
  (control.closest(".v") || host).prepend(tick);
  setTimeout(() => tick.remove(), 1500);
}

async function save(change, control, revert) {
  try {
    const r = await api("POST", "/api/settings", change);
    settings = r.settings;
    render();
    if (control) flash(control);
    pollNow();
  } catch (e) {
    toast(S.couldntSave(e.message));
    revert && revert();
    render();
  }
}

// Controls -------------------------------------------------------------------------------------------------
function fillSelects() {
  for (const sel of $$("select[data-key]")) {
    const key = sel.dataset.key;
    sel.replaceChildren(...OPTIONS[key].map((v) => el("option", { value: v === null ? "null" : String(v), text: label(key, v) })));
    sel.addEventListener("change", () => {
      const v = sel.value === "null" ? null : Number(sel.value);
      const before = get(settings, key);
      save(partial(key, v), sel, () => { sel.value = String(before); });
    });
  }
  for (const sw of $$("input.switch[data-key]")) {
    sw.addEventListener("change", () => save(partial(sw.dataset.key, sw.checked), sw, () => { sw.checked = !sw.checked; }));
  }
  for (const r of $$("input[type=radio][data-key]")) {
    r.addEventListener("change", () => r.checked && save(partial(r.dataset.key, r.value), r));
  }
  // Quiet hours: a toggle plus two times.
  const qt = $("#quiet-toggle");
  qt.addEventListener("change", () => {
    const q = qt.checked ? { start: $("#quiet-start").value || "23:00", end: $("#quiet-end").value || "07:00" } : null;
    save({ slideshow: { quiet_hours: q } }, qt, () => { qt.checked = !qt.checked; });
  });
  for (const t of [$("#quiet-start"), $("#quiet-end")]) {
    t.addEventListener("change", () => {
      if (!qt.checked || !$("#quiet-start").value || !$("#quiet-end").value) return;
      save({ slideshow: { quiet_hours: { start: $("#quiet-start").value, end: $("#quiet-end").value } } }, t);
    });
  }
  // Developer mode asks first.
  const dev = $("#dev-toggle");
  dev.addEventListener("change", () => {
    if (dev.checked) {
      dev.checked = false;
      openDialog($("#dev-dialog"), dev);
    } else {
      save({ dev_mode: false }, dev, () => { dev.checked = true; });
    }
  });
  $("#dev-confirm").addEventListener("click", () => {
    $("#dev-dialog").close();
    save({ dev_mode: true }, dev);
  });
}

function render() {
  if (!settings) return;
  for (const sel of $$("select[data-key]")) {
    const v = get(settings, sel.dataset.key);
    sel.value = v === null ? "null" : String(v);
    if (sel.selectedIndex < 0) {  // a value set elsewhere that isn't on the list
      sel.append(el("option", { value: String(v), text: label(sel.dataset.key, v) }));
      sel.value = String(v);
    }
  }
  for (const sw of $$("input.switch[data-key]")) sw.checked = Boolean(get(settings, sw.dataset.key));
  for (const r of $$("input[type=radio][data-key]")) r.checked = get(settings, r.dataset.key) === r.value;
  const q = settings.slideshow.quiet_hours;
  $("#quiet-toggle").checked = Boolean(q);
  $("#quiet-times").hidden = !q;
  if (q) {
    $("#quiet-start").value = q.start;
    $("#quiet-end").value = q.end;
  }
  $("#dev-toggle").checked = settings.dev_mode;
  const sleep = settings.sleep_min;
  $("#sleep-note").hidden = !(sleep !== null && sleep >= settings.auto_off_min);
  $("#saver-auto-label").textContent = S.turnOnAt(Math.max(20, settings.low_battery_shutdown_pct + 5));
  $("#hs-name").textContent = settings.hotspot.ssid;
  if ($("#hs-show").textContent === "Hide") $("#hs-pass").textContent = settings.hotspot.password;
  renderSaver();
  $("#settings-root").setAttribute("aria-busy", "false");
}

function renderSaver() {
  if (!settings) return;
  const st = lastStatus;
  const auto = st && st.battery_saver && st.battery_saver.why === "auto";
  $("#saver-toggle").checked = Boolean(settings.battery_saver.enabled || auto);
  $("#saver-sub").textContent = auto ? S.saverAuto : settings.battery_saver.enabled ? S.saverOn : S.saverOff;
}

// Status rows ------------------------------------------------------------------------------------------------
function renderStatus(st) {
  lastStatus = st;
  const b = st.battery || {};
  const bat = $("#s-battery");
  bat.replaceChildren();
  if (b.level !== null && b.level !== undefined) {
    const meter = el("span", { class: "meter", "aria-hidden": "true" }, el("i"));
    meter.firstChild.style.width = `${b.level}%`;
    bat.append(meter, `${b.level}%`);
    if (b.plugged) bat.append(el("span", { class: "ok", text: " ⚡", "aria-label": "charging" }));
  } else bat.textContent = "–";
  const disp = $("#s-display");
  if (!st.module_present) disp.textContent = S.noModuleShort;
  else if (st.display && st.display.state === "developing") disp.textContent = S.developingShort;
  else {
    disp.replaceChildren(S.moduleIn);
    if (st.current) disp.append(" · ", el("img", { class: "thumb", src: `/thumbs/${st.current}.jpg`, alt: "" }));
  }
  const camText = { ready: S.ready, fallback: S.ready, asleep: `${S.asleep} · ${S.asleepSub}`, waking: S.starting,
    starting: S.starting, off: S.starting, failed: S.problem }[st.camera] || st.camera;
  const camErr = (st.errors || []).find((e) => e.code === "camera");
  $("#s-camera").textContent = camErr ? `${S.problem}: ${camErr.message}` : camText;
  const sh = st.share || {};
  if (sh.mode) {
    $("#s-sharing").textContent = sh.mode === "hotspot" ? `${S.cameraWifi} “${sh.ssid || ""}”` : sh.ssid || "";
    $("#s-sharing-sub").textContent = sh.on_battery ? S.stopsAfter(sh.timeout_min) : S.staysOn;
    $("#s-stop-sharing").hidden = false;
  } else {
    $("#s-sharing").textContent = S.notSharing;
    $("#s-sharing-sub").textContent = "";
    $("#s-stop-sharing").hidden = true;
  }
  // Time
  $("#s-time").textContent = `${(st.time || "").slice(11, 16)} · ${st.tz || ""}`;
  const drift = st.time ? (Date.parse(st.time) - Date.now()) / 1000 : 0;
  const driftEl = $("#s-drift");
  if (!st.clock_valid) {
    driftEl.hidden = false;
    driftEl.textContent = S.clockUnset;
  } else if (Math.abs(drift) > 60) {
    const m = `${Math.round(Math.abs(drift) / 60)} min`;
    driftEl.hidden = false;
    driftEl.textContent = drift < 0 ? S.behind(m) : S.ahead(m);
  } else driftEl.hidden = true;
  // Storage
  const s = st.storage || {};
  if (s.total_bytes) {
    const used = s.total_bytes - s.free_bytes;
    $("#s-storage-bar").style.width = `${Math.round((used / s.total_bytes) * 100)}%`;
    $("#s-storage").textContent = S.freeAbout(s.free_bytes, s.photos_left);
    $("#s-storage-2").textContent = S.usedLine(st.photo_count, s.used_bytes || 0);
  }
  $("#s-version").textContent = st.version;
  $("#check-update").disabled = sh.mode !== "home";
  if (sh.mode !== "home") $("#update-sub").textContent = S.needsInternet;
  else if ($("#update-sub").textContent === S.needsInternet) $("#update-sub").textContent = "";
  $("#s-about").textContent = `${st.model || ""} · ${location.host}`;
  renderSaver();
}

// Wi-Fi --------------------------------------------------------------------------------------------------------
let wifiInfo = null;

async function loadWifi() {
  try {
    wifiInfo = await api("GET", "/api/wifi");
  } catch (e) {
    return;
  }
  const ul = $("#nets");
  ul.replaceChildren(...wifiInfo.home.map((h) => el("li", {}, el("span", { text: h.ssid }),
    el("button", { class: "tbtn small", type: "button", text: S.forget, onclick: () => askForget(h) }))));
  const full = wifiInfo.home.length >= wifiInfo.max;
  $("#add-net").disabled = full;
  $("#nets-full").hidden = !full;
}

let forgetting = null;
function askForget(h) {
  forgetting = h;
  $("#forget-text").textContent = S.forgetText(h.ssid);
  openDialog($("#forget-dialog"), document.activeElement);
}
$("#forget-confirm").addEventListener("click", async () => {
  $("#forget-dialog").close();
  try {
    await api("DELETE", `/api/wifi/home/${forgetting.n}`);
    toast(S.networkForgotten);
    loadWifi();
  } catch (e) {
    toast(e.message);
  }
});

$("#add-net").addEventListener("click", () => {
  $("#net-form").reset();
  $("#net-ssid-err").textContent = "";
  $("#net-pass-err").textContent = "";
  $("#net-24").hidden = !(wifiInfo && wifiInfo.wifi_5ghz === false);
  openDialog($("#net-dialog"), $("#add-net"));
});
$("#net-open").addEventListener("change", () => {
  $("#net-pass").disabled = $("#net-open").checked;
});
$("#net-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ssid = $("#net-ssid").value;
  const open = $("#net-open").checked;
  const password = open ? null : $("#net-pass").value;
  $("#net-ssid-err").textContent = ssid ? "" : S.needName;
  $("#net-pass-err").textContent = open || (password.length >= 8 && password.length <= 63) ? "" : S.needPassword;
  if (!ssid || (!open && $("#net-pass-err").textContent)) return;
  const d = $("#net-dialog");
  d.dataset.busy = "1";
  try {
    await api("POST", "/api/wifi/home", { ssid, password });
    delete d.dataset.busy;
    d.close();
    toast(S.networkAdded);
    loadWifi();
  } catch (err) {
    delete d.dataset.busy;
    const target = /password/i.test(err.message) ? "#net-pass-err" : "#net-ssid-err";
    $(target).textContent = err.message.replace(/^(ssid|password): /, "");
  }
});

$("#change-hotspot").addEventListener("click", () => {
  $("#hs-ssid").value = settings.hotspot.ssid;
  $("#hs-password").value = settings.hotspot.password;
  $("#hs-ssid-err").textContent = "";
  $("#hs-password-err").textContent = "";
  openDialog($("#hs-dialog"), $("#change-hotspot"));
});
$("#hs-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = $("#hs-dialog");
  d.dataset.busy = "1";
  try {
    const r = await api("POST", "/api/settings", { hotspot: { ssid: $("#hs-ssid").value, password: $("#hs-password").value } });
    settings = r.settings;
    delete d.dataset.busy;
    d.close();
    render();
    toast(S.saved);
  } catch (err) {
    delete d.dataset.busy;
    const field = err.message.startsWith("hotspot.password") ? "#hs-password-err" : "#hs-ssid-err";
    $(field).textContent = err.message.replace(/^hotspot\.\w+: /, "");
  }
});
$("#hs-show").addEventListener("click", () => {
  const show = $("#hs-show").textContent === "Show";
  $("#hs-show").textContent = show ? "Hide" : "Show";
  $("#hs-pass").textContent = show ? settings.hotspot.password : "••••••••";
});

// Other buttons ------------------------------------------------------------------------------------------------
$("#s-stop-sharing").addEventListener("click", async () => {
  try {
    await api("POST", "/api/share/stop");
    toast(S.sharingStopped);
    sharingEnded();
  } catch (e) {
    toast(e.message);
  }
});
$("#use-phone-time").addEventListener("click", () => setPhoneTime());
$("#s-light").addEventListener("click", () => openDialog($("#light-sheet"), $("#s-light")));

let latest = null;
$("#check-update").addEventListener("click", async () => {
  $("#update-sub").textContent = "…";
  try {
    const r = await api("GET", "/api/update");
    latest = r.latest;
    $("#update-sub").textContent = r.available ? S.available(r.latest) : S.upToDate;
    $("#start-update").hidden = !r.available;
  } catch (e) {
    $("#update-sub").textContent = e.message;
  }
});
$("#start-update").addEventListener("click", () => openDialog($("#update-dialog"), $("#start-update")));
$("#upd-confirm").addEventListener("click", async () => {
  $("#update-dialog").close();
  try {
    await api("POST", "/api/update", { version: latest });
    pollNow();
  } catch (e) {
    toast(e.message);
  }
});

// Setup check ----------------------------------------------------------------------------------------------------
function renderChecks(r) {
  const ul = $("#checks");
  if (!r || !r.checks || !r.checks.length) {
    ul.replaceChildren(el("li", {}, el("span", { text: "" }), el("span", { text: S.notRun })));
    return;
  }
  ul.replaceChildren(...r.checks.map((c) => {
    const li = el("li", {}, el("span", { class: c.ok ? "pass" : "fail", text: c.ok ? "✓" : "✕", "aria-label": c.ok ? S.pass : S.fail }),
      el("span", { text: `${c.name}${c.detail ? ` · ${c.detail}` : ""}` }));
    if (!c.ok && c.hint) li.append(el("span", { class: "h", text: c.hint }));
    return li;
  }));
}
$("#run-check").addEventListener("click", async () => {
  $("#run-check").disabled = true;
  try {
    renderChecks(await api("POST", "/api/selftest"));
    pollNow();
  } catch (e) {
    toast(e.message);
  } finally {
    $("#run-check").disabled = false;
  }
});

// Start --------------------------------------------------------------------------------------------------------------
fillSelects();
onStatus(renderStatus);
startPolling();
(async () => {
  try {
    settings = await api("GET", "/api/settings");
    render();
  } catch (e) {
    toast(e.message);
  }
  loadWifi();
  try {
    renderChecks(await api("GET", "/api/selftest"));
  } catch (e) { /* shown as not run */ }
  if (location.hash === "#checks") {
    $("details.adv").open = true;
    $("#checks").scrollIntoView();
  }
})();
