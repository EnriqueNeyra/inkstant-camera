// The gallery page (SPEC.md §11.4.1, §11.4.3, §11.4.4): grid, scrolling, select mode, downloads.
import {
  $, $$, api, dayLabel, deviceKind, el, icon, layoutVars, monthLabel, onStatus, openDialog,
  pollNow, sharingEnded, startPolling, status, statusLine, toast,
} from "./common.js";
import { S, fmtBytes } from "./strings.js";
import { Viewer, confirmDelete } from "./viewer.js";

const root = $("#grid-root");
let nextBefore = root.dataset.next ? Number(root.dataset.next) : null;
let total = Number(root.dataset.total || 0);
let loadingMore = false;
let today = null;
let selecting = false;
let selectAll = false;
const selected = new Set();

// The photos on the page, in order (newest first).
function tiles() {
  return $$(".tile", root);
}
function photoOf(t) {
  return { id: t.dataset.id, seq: Number(t.dataset.seq), size: Number(t.dataset.size), day: t.dataset.day || null, time: t.dataset.time || null };
}
function list() {
  return tiles().map(photoOf);
}

// Rendering ------------------------------------------------------------------------------------
function tileFor(p) {
  const a = el("a", { class: "tile", href: `/p/${p.id}`, dataset: { id: p.id, seq: p.seq, size: p.size, day: p.day || "", time: p.time || "" } },
    el("img", { src: `/thumbs/${p.id}.jpg`, alt: "", width: 480, height: 270, loading: "lazy", decoding: "async" }));
  decorate(a);
  return a;
}

function groupFor(key, count) {
  const sec = el("section", { class: "dayg", dataset: { day: key } },
    el("h2", { class: "day" },
      el("b", { class: "dlabel", dataset: { day: key }, text: dayLabel(key, today) }),
      el("span", { class: "dcount", text: count ?? "" }),
      el("button", { class: "tbtn dsel", type: "button", hidden: !selecting, text: S.select })),
    el("div", { class: "grid" }));
  return sec;
}

function appendPhotos(photos, counts) {
  const loading = $("#loading");
  for (const p of photos) {
    const key = p.day || "unknown";
    let groups = $$(".dayg", root);
    let last = groups[groups.length - 1];
    if (!last || last.dataset.day !== key) {
      last = groupFor(key, counts ? counts[key] : "");
      root.insertBefore(last, loading);
    }
    $(".grid", last).append(tileFor(p));
  }
  $("#empty")?.remove();
  refreshLabels();
}

function refreshLabels() {
  for (const b of $$(".dlabel", root)) b.textContent = dayLabel(b.dataset.day, today);
  for (const b of $$(".dlabel-plain")) b.textContent = dayLabel(b.dataset.day, today);
  for (const t of tiles()) {
    const p = photoOf(t);
    t.setAttribute("aria-label", `Photo, ${dayLabel(p.day || "unknown", today)}${p.time ? `, ${p.time}` : ""}`);
  }
}

// Badges: on camera, developing (§11.4.1) -------------------------------------------------------------
function decorate(t) {
  const st = status();
  t.querySelector(".badge")?.remove();
  if (st) {
    const dev = st.display && st.display.state === "developing" && st.display.id === t.dataset.id;
    if (dev) t.append(el("span", { class: "badge dev", text: "Developing" }));
    else if (st.current === t.dataset.id) t.append(el("span", { class: "badge" }, icon("display"), "On camera"));
  }
  if (selecting && !t.querySelector(".chk")) t.append(el("span", { class: "chk", "aria-hidden": "true" }, icon("check")));
  if (!selecting) t.querySelector(".chk")?.remove();
  t.classList.toggle("sel", selecting && (selectAll || selected.has(t.dataset.id)));
  if (selecting) t.setAttribute("aria-pressed", t.classList.contains("sel") ? "true" : "false");
  else t.removeAttribute("aria-pressed");
}

// Loading more as you scroll (§11.4.1) -------------------------------------------------------------------
async function loadMore() {
  if (loadingMore || nextBefore === null) return false;
  loadingMore = true;
  $("#loading").hidden = false;
  try {
    const r = await api("GET", `/api/photos?before=${nextBefore}&limit=60`);
    appendPhotos(r.photos);
    nextBefore = r.next_before;
    total = r.total;
    $("#end").hidden = nextBefore !== null;
    return r.photos.length > 0;
  } catch (e) {
    return false;
  } finally {
    loadingMore = false;
    $("#loading").hidden = true;
  }
}

const io = new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) loadMore();
}, { rootMargin: "0px 0px 150% 0px" });
io.observe($("#end"));
io.observe($("#loading"));
const sentinel = el("div", { class: "sentinel", "aria-hidden": "true" });
root.append(sentinel);
io.observe(sentinel);

// New photos while open: prepend if at the top, else a pill. ---------------------------------------------
let pendingNew = 0;
async function prependNew() {
  const top = tiles()[0];
  const after = top ? Number(top.dataset.seq) : 0;
  const r = await api("GET", `/api/photos?after=${after}&limit=200`);
  for (const p of r.photos.slice().reverse()) {
    const key = p.day || "unknown";
    let first = $(".dayg", root);
    if (!first || first.dataset.day !== key) {
      first = groupFor(key, "");
      root.insertBefore(first, $(".dayg", root) || $("#loading"));
    }
    $(".grid", first).prepend(tileFor(p));
  }
  total = r.total;
  $("#empty")?.remove();
  refreshLabels();
  pendingNew = 0;
  $("#new-pill").hidden = true;
}

$("#new-pill").addEventListener("click", async () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
  await prependNew();
});

// Status ----------------------------------------------------------------------------------------------------
onStatus(async (st) => {
  statusLine(st);
  const first = today === null;
  today = st.today;
  if (first) refreshLabels();
  for (const t of tiles()) decorate(t);
  viewer?.updateShow(st);
  const top = tiles()[0];
  const topSeq = top ? Number(top.dataset.seq) : 0;
  if (st.newest_seq > topSeq && !jumped) {
    if (window.scrollY < 80 && !selecting) await prependNew();
    else {
      pendingNew = st.newest_seq - topSeq;
      $("#new-pill").textContent = S.newPhotos(pendingNew);
      $("#new-pill").hidden = false;
    }
  }
  layoutVars();
});

// The viewer ------------------------------------------------------------------------------------------------
const viewer = new Viewer({
  list,
  total: () => total,
  more: loadMore,
  onDeleted: (ids) => removeTiles(ids),
});

root.addEventListener("click", (e) => {
  const t = e.target.closest(".tile");
  if (t) {
    e.preventDefault();
    if (selecting) toggle(t);
    else viewer.open(t.dataset.id);
    return;
  }
  const dsel = e.target.closest(".dsel");
  if (dsel) selectDay(dsel.closest(".dayg"));
});

function removeTiles(ids) {
  for (const id of ids) {
    const t = $(`.tile[data-id="${id}"]`, root);
    if (!t) continue;
    const g = t.closest(".dayg");
    t.remove();
    total -= 1;
    const c = $(".dcount", g);
    if (c.textContent) c.textContent = String(Math.max(0, Number(c.textContent) - 1));
    if (!$(".tile", g)) g.remove();
  }
  if (!tiles().length && nextBefore === null) location.reload();
}

// Select mode (§11.4.3) -----------------------------------------------------------------------------------------
function setSelecting(on) {
  selecting = on;
  selectAll = false;
  selected.clear();
  document.body.classList.toggle("selecting", on);
  $("#appbar .bar:not(.selbar)").hidden = on;
  $("#selbar").hidden = !on;
  $("#actions").hidden = !on;
  for (const b of $$(".dsel", root)) b.hidden = !on;
  for (const t of tiles()) decorate(t);
  updateSelection();
  layoutVars();
}

function toggle(t) {
  if (selectAll) {
    selectAll = false;
    for (const x of tiles()) selected.add(x.dataset.id);
  }
  if (selected.has(t.dataset.id)) selected.delete(t.dataset.id);
  else selected.add(t.dataset.id);
  decorate(t);
  updateSelection();
}

async function selectDay(g) {
  const key = g.dataset.day;
  const want = Number($(".dcount", g).textContent || 0);
  // Load until the whole day is on the page, so "a day" really means all of it.
  while (want && $$(".tile", g).length < want && (await loadMore())) {
    g = $(`.dayg[data-day="${key}"]`, root);
  }
  const groupTiles = $$(`.dayg[data-day="${key}"] .tile`, root);
  const allIn = groupTiles.every((t) => selectAll || selected.has(t.dataset.id));
  if (selectAll) {
    selectAll = false;
    for (const x of tiles()) selected.add(x.dataset.id);
  }
  for (const t of groupTiles) {
    if (allIn) selected.delete(t.dataset.id);
    else selected.add(t.dataset.id);
    decorate(t);
  }
  updateSelection();
}

function selectedPhotos() {
  return selectAll ? list() : list().filter((p) => selected.has(p.id));
}

function selectionCount() {
  return selectAll ? total : selected.size;
}

function selectionBytes() {
  if (selectAll) {
    const st = status();
    return st && st.storage ? st.storage.used_bytes : list().reduce((a, p) => a + p.size, 0);
  }
  return selectedPhotos().reduce((a, p) => a + p.size, 0);
}

function updateSelection() {
  const n = selectionCount();
  $("#sel-count").textContent = n ? S.selected(n) : S.selectPhotos;
  $("#act-size").textContent = n ? fmtBytes(selectionBytes()) : "";
  $("#act-download").disabled = !n;
  $("#act-delete").disabled = !n;
  $("#act-show").disabled = n !== 1;
  for (const g of $$(".dayg", root)) {
    const ts = $$(".tile", g);
    const all = ts.length && ts.every((t) => selectAll || selected.has(t.dataset.id));
    const b = $(".dsel", g);
    if (b) b.textContent = all ? S.deselect : S.select;
  }
}

$("#select-btn").addEventListener("click", () => setSelecting(true));
$("#sel-cancel").addEventListener("click", () => setSelecting(false));
$("#sel-all").addEventListener("click", () => {
  selectAll = true;
  selected.clear();
  for (const t of tiles()) decorate(t);
  updateSelection();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && selecting && $("#viewer").hidden && !document.querySelector("dialog[open]")) setSelecting(false);
});

// Actions ---------------------------------------------------------------------------------------------------------
$("#act-show").addEventListener("click", async () => {
  const [p] = selectedPhotos();
  if (!p) return;
  try {
    const r = await api("POST", `/api/photos/${p.id}/show`);
    toast(r && r.queued === "next" ? S.queued : S.developing);
    setSelecting(false);
  } catch (e) {
    toast(e.message);
  }
});

$("#act-delete").addEventListener("click", async () => {
  const n = selectionCount();
  const st = status();
  const ids = selectedPhotos().map((p) => p.id);
  const onCamera = Boolean(st && st.current && (selectAll || ids.includes(st.current)));
  if (!(await confirmDelete(n, onCamera))) return;
  try {
    const r = selectAll
      ? await api("POST", "/api/photos/delete", { all: true })
      : await api("POST", "/api/photos/delete", { ids });
    toast(r.deleted.length === 1 ? S.photoDeleted : S.photosDeleted(r.deleted.length));
    if (selectAll) location.reload();
    removeTiles(r.deleted);
    setSelecting(false);
  } catch (e) {
    toast(e.message);
  }
});

$("#act-download").addEventListener("click", () => {
  const photos = selectedPhotos();
  if (!selectAll && photos.length === 1) {
    location.href = `/photos/${photos[0].id}.jpg?dl=1`;
    setSelecting(false);
    return;
  }
  openDownload({ count: selectionCount(), bytes: selectionBytes(), ids: selectAll ? null : photos.map((p) => p.id), all: selectAll });
});

// Download sheet (§11.4.4) -------------------------------------------------------------------------------------------
function openDownload({ count, bytes, ids, all }) {
  $("#dl-title").textContent = S.downloadN(count);
  $("#dl-meta").textContent = S.downloadMeta(bytes, (bytes / 3e6) * 1.3);
  const kind = deviceKind();
  $("#dl-howto").textContent = kind === "ios" ? S.howtoIos : kind === "android" ? S.howtoAndroid : S.howtoComputer;
  $("#dl-big").hidden = bytes < 1e9;
  $("#dl-ids").value = ids ? ids.join(",") : "";
  $("#dl-set").value = all ? "all" : "";
  $("#dl-day").value = "";
  openDialog($("#download-sheet"), document.activeElement);
}

$("#dl-form").addEventListener("submit", () => {
  // The browser takes over the download; the page stays.
  setTimeout(() => {
    $("#download-sheet").close();
    toast(S.downloadStarted);
    if (selecting) setSelecting(false);
  }, 50);
});

// The ⋯ menu -----------------------------------------------------------------------------------------------------------
const menu = $("#menu");
$("#menu-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  menu.hidden = !menu.hidden;
  $("#menu-btn").setAttribute("aria-expanded", String(!menu.hidden));
  if (!menu.hidden) menu.querySelector("button").focus();
});
document.addEventListener("click", (e) => {
  if (!menu.hidden && !menu.contains(e.target)) {
    menu.hidden = true;
    $("#menu-btn").setAttribute("aria-expanded", "false");
  }
});
menu.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  menu.hidden = true;
  if (b.dataset.act === "download-all") {
    const st = status();
    openDownload({ count: total, bytes: st && st.storage ? st.storage.used_bytes : 0, ids: null, all: true });
  } else if (b.dataset.act === "jump") {
    openMonths();
  } else if (b.dataset.act === "stop-sharing") {
    try {
      await api("POST", "/api/share/stop");
      toast(S.sharingStopped);
      sharingEnded();
    } catch (err) {
      toast(err.message);
    }
  }
});

// Jump to month ------------------------------------------------------------------------------------------------------------
let jumped = false;
async function openMonths() {
  const ul = $("#month-list");
  ul.replaceChildren();
  try {
    const months = await api("GET", "/api/months");
    for (const m of months) {
      ul.append(el("li", {}, el("button", { type: "button", onclick: () => jumpTo(m) },
        el("span", { text: monthLabel(m.month) }), el("small", { text: String(m.count) }))));
    }
  } catch (e) {
    toast(e.message);
    return;
  }
  openDialog($("#month-sheet"), $("#menu-btn"));
}

async function jumpTo(m) {
  $("#month-sheet").close();
  const r = await api("GET", `/api/photos?month=${m.month || "none"}&limit=60`);
  for (const g of $$(".dayg", root)) g.remove();
  jumped = true;
  appendPhotos(r.photos);
  nextBefore = r.next_before;
  $("#end").hidden = nextBefore !== null;
  $("#newest-pill").hidden = false;
  window.scrollTo({ top: 0 });
}
$("#newest-pill").addEventListener("click", () => { location.href = "/"; });

// Start ----------------------------------------------------------------------------------------------------------------------
refreshLabels();
for (const t of tiles()) decorate(t);
layoutVars();
startPolling();
if (new URLSearchParams(location.search).get("select") === "1") setSelecting(true);
// A direct /p/<id> link in the address bar while on the gallery: open it.
window.addEventListener("pageshow", () => pollNow());
