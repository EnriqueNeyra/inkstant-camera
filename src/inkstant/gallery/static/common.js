// Shared by every page: API calls, the status poll, banners, toasts, dialogs and dates.
import { S } from "./strings.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

// Every request that changes something carries X-Inkstant.
export async function api(method, path, body, { poll = false } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-Inkstant"] = "1";
  if (poll) headers["X-Inkstant-Poll"] = "1";
  let opts = { method, headers, cache: "no-store" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (e) {
    noteUnreachable(poll);
    throw new ApiError("unreachable", "Can't reach the camera.", 0);
  }
  noteReachable();
  if (resp.status === 204) return null;
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  if (!resp.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(err.code || "error", err.message || `Something went wrong (${resp.status}).`, resp.status);
  }
  return data;
}

// Icons --------------------------------------------------------------------------------
export function icon(name, cls = "i") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined) e.append(c);
  return e;
}

// Toasts --------------------------------------------------------------------------------
export function toast(text) {
  const box = $("#toasts");
  if (!box) return;
  const t = el("div", { class: "toast", text });
  box.append(t);
  setTimeout(() => t.remove(), 3000);
}

// Dialogs -------------------------------------------------------------------------------
export function wireDialogs(root = document) {
  for (const d of $$("dialog", root)) {
    d.addEventListener("click", (e) => {
      if (e.target === d && !d.dataset.busy) d.close(); // the backdrop
      if (e.target.closest("[data-close]") && !d.dataset.busy) d.close();
    });
  }
  for (const b of $$("[data-toggle]", root)) {
    b.addEventListener("click", () => {
      const input = document.getElementById(b.dataset.toggle);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      b.textContent = show ? "Hide" : "Show";
    });
  }
}

export function openDialog(d, opener) {
  d.showModal();
  d.addEventListener("close", () => opener && opener.focus && opener.focus(), { once: true });
}

// Dates: the camera's local time, shown in the phone's language. -------------------
export function parseDay(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function dayLabel(day, today) {
  if (!day || day === "unknown") return S.unknownDate;
  if (today) {
    if (day === today) return S.today;
    const t = parseDay(today);
    t.setDate(t.getDate() - 1);
    const y = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
    if (day === y) return S.yesterday;
  }
  const d = parseDay(day);
  const thisYear = today ? Number(today.slice(0, 4)) : new Date().getFullYear();
  const opts = d.getFullYear() === thisYear
    ? { weekday: "short", day: "numeric", month: "short" }
    : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat(undefined, opts).format(d);
}

export function longWhen(day, time) {
  if (!day) return S.unknownDate;
  const d = parseDay(day);
  const date = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" }).format(d);
  return time ? `${date} · ${time}` : date;
}

export function monthLabel(month) {
  if (!month) return S.unknownDate;
  const [y, m] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(new Date(y, m - 1, 1));
}

// Device (only for the download instructions) ------------------------------------------------
export function deviceKind() {
  const ua = navigator.userAgent;
  if (/Android/.test(ua)) return "android";
  // iPadOS reports itself as a Mac; a touch screen gives it away.
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
  return "computer";
}

// Status poll -------------------------------------------------------------------------
let failures = 0;
let lastStatus = null;
const listeners = [];

export function onStatus(fn) {
  listeners.push(fn);
  if (lastStatus) fn(lastStatus);
}

export function status() {
  return lastStatus;
}

// "Can't reach the camera": two status polls in a row fail, or any action fails to connect.
function noteUnreachable(poll) {
  failures += 1;
  if (!poll || failures >= 2) showOffline(true);
}

function noteReachable() {
  if (failures) {
    failures = 0;
    showOffline(false);
  }
}

function showOffline(on) {
  const card = $("#offline");
  if (!card || !$("#ended").hidden || !$("#updating").hidden) return;
  card.hidden = !on;
}

export function sharingEnded() {
  $("#ended").hidden = false;
  stopPolling = true;
}

let stopPolling = false;
let timer = null;

export async function pollNow() {
  if (stopPolling) return null;
  try {
    const st = await api("GET", "/api/status", undefined, { poll: true });
    lastStatus = st;
    for (const fn of listeners) fn(st);
    renderBanners(st);
    renderUpdating(st);
    return st;
  } catch (e) {
    return null;
  }
}

export function startPolling() {
  const tick = async () => {
    clearTimeout(timer);
    if (document.visibilityState === "visible") await pollNow();
    timer = setTimeout(tick, 10000);
  };
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tick();
  });
  $("#offline-retry")?.addEventListener("click", () => pollNow());
  tick();
}

function renderUpdating(st) {
  const box = $("#updating");
  if (!box) return;
  const u = st.update || {};
  box.hidden = u.state !== "running";
  if (u.state === "running") {
    const order = ["downloading", "installing", "restarting"];
    const at = order.indexOf(u.step || "downloading");
    $$("#update-steps li").forEach((li, i) => {
      li.className = i < at ? "done" : i === at ? "now" : "";
    });
    if (u.step === "restarting") $("#update-note").textContent = "The camera is restarting. Turn sharing on again to reconnect.";
  }
}

// Banners: errors first, at most two, then "N more". ------------------------------------
let bannersExpanded = false;

export function renderBanners(st) {
  const box = $("#banners");
  if (!box) return;
  const items = [];
  const err = (text, action) => items.push({ cls: "err", icon: "error", text, action });
  const warn = (text, action) => items.push({ cls: "warn", icon: "warn", text, action });
  const codes = new Map((st.errors || []).map((e) => [e.code, e]));
  if (st.storage && st.storage.state === "full") err(S.storageFull, { label: S.selectPhotosAction, href: "/?select=1" });
  if (codes.has("camera")) err(S.cameraError(codes.get("camera").message));
  if (codes.has("display")) err(S.displayError);
  if (codes.has("storage")) err(codes.get("storage").message);
  if (st.setup_check && st.setup_check.passed === false) {
    err(S.setupFailed((st.setup_check.failed || []).join(", ")), { label: S.seeDetails, href: "/settings#checks" });
  }
  if (st.update && st.update.state === "failed") {
    err(S.updateFailed(st.update.message || "unknown error", st.version), null);
  }
  if (st.storage && st.storage.state === "low") warn(S.storageLow(st.storage.photos_left));
  if (st.battery && st.battery.low) warn(S.batteryLow(st.battery.level));
  if (st.clock_valid === false) warn(S.clockUnset, { label: S.usePhoneTime, act: "time" });
  if (st.update && st.update.state === "running") items.push({ cls: "info", icon: "refresh", text: S.updating });

  box.replaceChildren();
  const shown = bannersExpanded ? items : items.slice(0, 2);
  for (const it of shown) {
    const b = el("div", { class: `banner ${it.cls}`, role: it.cls === "err" ? "alert" : "status" },
      icon(it.icon), el("span", { class: "txt", text: it.text }));
    if (it.action) {
      if (it.action.href) b.append(el("a", { class: "tbtn", href: it.action.href, text: it.action.label }));
      else b.append(el("button", { class: "tbtn", type: "button", text: it.action.label, onclick: () => setPhoneTime() }));
    }
    box.append(b);
  }
  if (!bannersExpanded && items.length > 2) {
    box.append(el("button", { class: "banner more", type: "button", text: S.oneMore(items.length - 2),
      onclick: () => { bannersExpanded = true; renderBanners(st); } }));
  }
  layoutVars();
}

export async function setPhoneTime() {
  let tz = null;
  try {
    tz = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch (e) {
    tz = null;
  }
  try {
    await api("POST", "/api/time", { epoch_ms: Date.now(), tz });
    toast(S.timeSet);
    pollNow();
  } catch (e) {
    toast(S.couldntSave(e.message));
  }
}

// Keep sticky offsets right as the header grows or shrinks.
export function layoutVars() {
  const bar = $("#appbar") || $(".appbar");
  if (bar) document.documentElement.style.setProperty("--appbar-h", `${bar.offsetHeight}px`);
}
window.addEventListener("resize", layoutVars);

export function statusLine(st) {
  const line = $("#status-line");
  if (!line || !st) return;
  const parts = [el("span", { text: S.photos(st.photo_count) })];
  if (st.battery && st.battery.level !== null && st.battery.level !== undefined) {
    const bat = el("span", { class: "batt" }, icon("battery", "i sm"), ` ${st.battery.level}%`);
    if (st.battery.plugged) bat.append(icon("bolt", "i sm ok"));
    parts.push(el("span", { class: "dot", text: "·", "aria-hidden": "true" }), bat);
  }
  if (st.battery_saver && st.battery_saver.on) parts.push(el("span", { class: "saver", text: S.saver }));
  if (st.share && st.share.mode) {
    const name = st.share.mode === "hotspot" ? S.cameraWifi : st.share.ssid || "";
    parts.push(el("span", { class: "dot", text: "·", "aria-hidden": "true" }), el("span", {}, icon("wifi", "i sm"), ` ${name}`));
  }
  line.replaceChildren(...parts);
}

wireDialogs();
