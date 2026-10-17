// The photo viewer: over the gallery, or on its own at /p/<id>.
import { $, api, deviceKind, el, longWhen, openDialog, status, toast } from "./common.js";
import { S, fmtBytes, fmtInt } from "./strings.js";

const TIP_KEY = "inkstant.tip.v1";

function storeGet(k) {
  try { return localStorage.getItem(k); } catch (e) { return null; }
}
function storeSet(k, v) {
  try { localStorage.setItem(k, v); } catch (e) { /* private mode */ }
}

export function confirmDelete(n, includesOnCamera) {
  const d = $("#delete-dialog");
  $("#del-title").textContent = S.deleteN(n);
  $("#del-oncamera").hidden = !includesOnCamera;
  return new Promise((resolve) => {
    let ok = false;
    const confirm = () => { ok = true; d.close(); };
    $("#del-confirm").addEventListener("click", confirm, { once: true });
    d.addEventListener("close", () => {
      $("#del-confirm").removeEventListener("click", confirm);
      resolve(ok);
    }, { once: true });
    openDialog(d, document.activeElement);
  });
}

export class Viewer {
  // list(): the photos in order (newest first); total(): how many exist; more(): load more, returns bool.
  constructor({ list, total, more = async () => false, onDeleted = () => {}, standalone = false }) {
    this.list = list;
    this.total = total;
    this.more = more;
    this.onDeleted = onDeleted;
    this.standalone = standalone;
    this.root = $("#viewer");
    this.full = $("#v-full");
    this.thumb = $("#v-thumb");
    this.index = 0;
    this.mode = "photo"; // or "print": the photo as the camera's display shows it
    this.pushed = false;
    this.preloaded = new Map();
    this._wire();
  }

  _wire() {
    $("#v-close").addEventListener("click", () => this.close());
    $("#v-gone-close").addEventListener("click", () => this.close());
    $("#v-info-btn").addEventListener("click", () => this.info());
    $("#v-show").addEventListener("click", () => this.showOnCamera());
    $("#v-delete").addEventListener("click", () => this.remove());
    $("#v-tip-ok").addEventListener("click", () => { $("#v-tip").hidden = true; storeSet(TIP_KEY, "1"); });
    $("#v-stage").addEventListener("click", (e) => {
      if (this._swiped) return;
      if (e.target === this.full || e.target === this.thumb || e.target.id === "v-stage") this.root.classList.toggle("bare");
    });
    this.full.addEventListener("load", () => {
      this.full.classList.add("loaded");
      $("#v-stage").classList.add("ready");
      this._preloadNeighbours();
    });
    this.full.addEventListener("error", () => {
      if (this.root.hidden || !this.full.getAttribute("src")) return;
      if (this.mode === "print") {
        // Not made yet: an older photo whose print the camera is still making.
        toast(S.printNotReady);
        this.setMode("photo");
      } else {
        $("#v-gone").hidden = false;
      }
    });
    for (const r of document.querySelectorAll('input[name="vmode"]')) {
      r.addEventListener("change", () => { if (r.checked) this.setMode(r.value); });
    }
    document.addEventListener("keydown", (e) => {
      if (this.root.hidden || document.querySelector("dialog[open]")) return;
      if (e.key === "ArrowLeft") this.go(-1);
      else if (e.key === "ArrowRight") this.go(1);
      else if (e.key === "Escape") this.close();
    });
    window.addEventListener("popstate", () => {
      if (!this.root.hidden && this.pushed) {
        this.pushed = false;
        this._hide();
      }
    });
    // Swipe between photos, but never while pinch-zoomed.
    let x0 = null;
    let y0 = null;
    this.root.addEventListener("pointerdown", (e) => {
      this._swiped = false;
      const zoomed = window.visualViewport && window.visualViewport.scale > 1.01;
      x0 = zoomed || e.pointerType === "mouse" ? null : e.clientX;
      y0 = e.clientY;
    });
    this.root.addEventListener("pointerup", (e) => {
      if (x0 === null) return;
      const dx = e.clientX - x0;
      const dy = e.clientY - y0;
      x0 = null;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        this._swiped = true;
        this.go(dx < 0 ? 1 : -1);
      }
    });
  }

  photo() {
    return this.list()[this.index];
  }

  src(p) {
    return this.mode === "print" ? `/prints/${p.id}.png` : `/photos/${p.id}.jpg`;
  }

  setMode(mode) {
    this.mode = mode;
    for (const r of document.querySelectorAll('input[name="vmode"]')) r.checked = r.value === mode;
    this.preloaded.clear();
    this._render();
  }

  open(id, { push = true } = {}) {
    const i = this.list().findIndex((p) => p.id === id);
    this.index = Math.max(0, i);
    this.root.hidden = false;
    this.root.classList.remove("bare");
    document.body.classList.add("noscroll");
    if (push && !this.standalone) {
      history.pushState({ viewer: id }, "", `/p/${id}`);
      this.pushed = true;
    }
    this._render();
    if (deviceKind() !== "computer" && !storeGet(TIP_KEY)) $("#v-tip").hidden = false;
    $("#v-close").focus();
  }

  close() {
    if (this.standalone) {
      location.href = "/";
      return;
    }
    if (this.pushed) {
      this.pushed = false;
      history.back();
    }
    this._hide();
  }

  _hide() {
    this.root.hidden = true;
    document.body.classList.remove("noscroll");
    this.full.removeAttribute("src");
    const p = this.photo();
    if (p) document.querySelector(`.tile[data-id="${p.id}"]`)?.focus();
  }

  async go(step) {
    let i = this.index + step;
    if (i >= this.list().length && step > 0 && (await this.more())) i = this.index + step;
    if (i < 0 || i >= this.list().length) return;
    this.index = i;
    if (this.pushed) history.replaceState({ viewer: this.photo().id }, "", `/p/${this.photo().id}`);
    this._render();
  }

  _render() {
    const p = this.photo();
    if (!p) return;
    $("#v-gone").hidden = true;
    this.full.classList.remove("loaded");
    $("#v-stage").classList.remove("ready");
    // The blurred stand-in: the thumbnail for a photo; a print is small enough to be its own.
    this.thumb.src = this.mode === "print" ? this.src(p) : `/thumbs/${p.id}.jpg`;
    this.full.src = this.src(p);
    this.full.alt = `${this.mode === "print" ? "Print" : "Photo"}, ${longWhen(p.day, p.time)}`;
    $("#v-when").textContent = longWhen(p.day, p.time);
    $("#v-counter").textContent = S.photoOf(this.index + 1, this.total());
    $("#v-download").href = `${this.src(p)}?dl=1`;
    this.updateShow(status());
  }

  updateShow(st) {
    const btn = $("#v-show");
    let why = "";
    if (st && !st.module_present) why = S.noModule;
    else if (st && st.lifecycle !== "ACTIVE") why = S.cameraBusy;
    btn.disabled = Boolean(why);
    btn.setAttribute("aria-disabled", why ? "true" : "false");
    $("#v-show-why").textContent = why;
  }

  _preloadNeighbours() {
    for (const d of [1, -1]) {
      const p = this.list()[this.index + d];
      if (p && !this.preloaded.has(p.id)) {
        const img = new Image();
        img.src = this.src(p);
        this.preloaded.set(p.id, img);
        if (this.preloaded.size > 6) this.preloaded.delete(this.preloaded.keys().next().value);
      }
    }
  }

  async info() {
    const p = this.photo();
    const list = $("#info-list");
    list.replaceChildren();
    const add = (k, v) => list.append(el("dt", { text: k }), el("dd", { text: v }));
    add("Date", longWhen(p.day, p.time));
    add("File size", fmtBytes(p.size));
    try {
      const d = await api("GET", `/api/photos/${p.id}`);
      add("Dimensions", `${fmtInt(d.width)} × ${fmtInt(d.height)}`);
      if (d.exposure_s) add("Exposure", d.exposure_s >= 1 ? `${d.exposure_s.toFixed(1)} s` : `1/${Math.round(1 / d.exposure_s)} s`);
      if (d.iso) add("ISO", String(d.iso));
    } catch (e) {
      if (e.code === "not_found") $("#v-gone").hidden = false;
    }
    add("File name", `inkstant_${p.id}.jpg`);
    openDialog($("#info-sheet"), $("#v-info-btn"));
  }

  async showOnCamera() {
    const p = this.photo();
    try {
      const r = await api("POST", `/api/photos/${p.id}/show`);
      toast(r && r.queued === "next" ? S.queued : S.developing);
    } catch (e) {
      toast(e.message);
    }
  }

  async remove() {
    const p = this.photo();
    const st = status();
    const onCamera = Boolean(st && st.current === p.id);
    if (!(await confirmDelete(1, onCamera))) return;
    try {
      await api("POST", "/api/photos/delete", { ids: [p.id] });
      toast(S.photoDeleted);
      this.onDeleted([p.id]);
      if (!this.list().length) return this.close();
      if (this.index >= this.list().length) this.index = this.list().length - 1;
      if (this.standalone) return this.close();
      this._render();
    } catch (e) {
      toast(e.message);
    }
  }
}
