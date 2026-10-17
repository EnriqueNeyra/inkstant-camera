// A direct link to one photo, /p/<id>: the viewer on its own; closing goes to the gallery.
import { $, api, onStatus, startPolling } from "./common.js";
import { Viewer } from "./viewer.js";

const root = $("#photo-root");
const me = { id: root.dataset.id, seq: Number(root.dataset.seq), size: Number(root.dataset.size), day: root.dataset.day || null, time: root.dataset.time || null };
let photos = [me];
let total = 1;

async function neighbours() {
  try {
    const newer = await api("GET", `/api/photos?after=${me.seq}&limit=30`);
    const older = await api("GET", `/api/photos?before=${me.seq}&limit=30`);
    photos = [...newer.photos, me, ...older.photos];
    total = older.total;
    viewer.index = photos.findIndex((p) => p.id === me.id);
    viewer._render();
  } catch (e) {
    /* the single photo still works */
  }
}

const viewer = new Viewer({
  list: () => photos,
  total: () => total,
  standalone: true,
  onDeleted: (ids) => { photos = photos.filter((p) => !ids.includes(p.id)); },
});
viewer.open(me.id, { push: false });
onStatus((st) => viewer.updateShow(st));
startPolling();
neighbours();
