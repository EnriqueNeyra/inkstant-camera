"""The gallery's Flask app: pages, photos, the JSON API and ZIP downloads.

Runs as the unprivileged user inkstant-web. It reads photos and thumbnails directly; every
change (delete, settings, Wi-Fi, time, show on camera) goes through the camera app's
control socket, which checks each command.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file, url_for

from inkstant import ipc, store, version
from inkstant.gallery import zipstream
from inkstant.paths import Paths

PAGE = 60
ONE_YEAR = 31_536_000
ACTIVITY_EVERY = 10.0
ZIP_ACTIVITY_EVERY = 30.0
CSP = ("default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
HTTP_FOR = {"invalid": 400, "not_found": 404, "denied": 403, "no_module": 409, "not_ready": 409,
            "busy": 409, "full": 409, "battery": 409, "offline": 409, "failed": 502,
            "unavailable": 503, "timeout": 504}


def day_label_iso(when: datetime | None) -> str | None:
    return when.strftime("%Y-%m-%d") if when else None


def create_app(paths: Paths | None = None, *, control_sock: Path | None = None,
               hostname: str | None = None, call=None) -> Flask:
    paths = paths or Paths.default()
    sock = control_sock or paths.control_sock
    hostname = (hostname or socket.gethostname()).lower()
    photos = store.PhotoStore(paths, web_group=None)
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = None
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    ctl = call or (lambda cmd, args=None: ipc.call(sock, cmd, args or {}))
    activity = {"last": 0.0}
    lock = threading.Lock()

    def report_activity(force: bool = False) -> None:
        now = time.monotonic()
        with lock:
            if not force and now - activity["last"] < ACTIVITY_EVERY:
                return
            activity["last"] = now
        try:
            ctl("share.activity")
        except ipc.IpcError:
            pass

    def api_error(code: str, message: str, status: int | None = None):
        resp = jsonify({"error": {"code": code, "message": message}})
        resp.status_code = status or HTTP_FOR.get(code, 500)
        return resp

    def call_or_error(cmd: str, args: dict | None = None):
        try:
            return ctl(cmd, args or {}), None
        except ipc.IpcError as e:
            return None, api_error(e.code, e.message)

    # Request checks ------------------------------------------------------------------
    def host_ok(host: str) -> bool:
        h = host.strip().lower()
        if h.startswith("["):  # [IPv6]:port
            name = h[1:].split("]", 1)[0]
        elif h.count(":") == 1:  # name:port
            name = h.split(":", 1)[0]
        else:
            name = h
        if name in ("localhost", f"{hostname}.local"):
            return True
        try:
            ipaddress.ip_address(name)
            return True  # an IP literal can't be a rebinding attack's host name
        except ValueError:
            return False

    @app.before_request
    def checks():
        if not host_ok(request.host or ""):
            abort(421)
        if request.method in ("POST", "DELETE", "PUT", "PATCH") and request.path.startswith("/api/"):
            if request.headers.get("X-Inkstant") != "1":
                return api_error("denied", "Missing request header.", 403)
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1].lower() != (request.host or "").lower():
                return api_error("denied", "Wrong origin.", 403)
        if request.path != "/healthz" and request.headers.get("X-Inkstant-Poll") != "1" \
                and not request.path.startswith("/static/"):
            report_activity()
        return None

    @app.after_request
    def headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith(("/photos/", "/thumbs/")) and resp.status_code == 200:
            # An ID is never reused, so a photo never changes.
            resp.headers["Cache-Control"] = f"public, max-age={ONE_YEAR}, immutable"
        elif request.path.startswith("/prints/"):
            # Remade when the palette changes: revalidate (a cheap 304 through the ETag).
            resp.headers["Cache-Control"] = "no-cache"
        elif request.path.startswith("/static/"):
            # ES modules import each other by plain URL, so after an update they must revalidate
            # (a cheap 304 through the ETag) instead of being cached for a year.
            resp.headers["Cache-Control"] = "no-cache"
        elif request.path.startswith("/api/") or resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def helpers():
        def asset(name: str) -> str:
            return url_for("static", filename=name, v=version.VERSION)

        return {"asset": asset, "version": version.VERSION}

    # Pages ---------------------------------------------------------------------------------
    def photo_json(p: store.Photo) -> dict:
        return {"id": p.id, "seq": p.seq, "day": p.day, "time": p.time, "size": p.size}

    @app.get("/")
    def gallery_page():
        all_photos = photos.list()
        first = all_photos[:PAGE]
        groups: list[dict] = []
        for p in first:
            key = p.day or "unknown"
            if not groups or groups[-1]["key"] != key:
                groups.append({"key": key, "photos": []})
            groups[-1]["photos"].append(p)
        counts: dict[str, int] = {}
        for p in all_photos:
            counts[p.day or "unknown"] = counts.get(p.day or "unknown", 0) + 1
        nxt = first[-1].seq if len(all_photos) > PAGE else None
        return render_template("gallery.html", groups=groups, counts=counts, total=len(all_photos),
                               next_before=nxt, oldest=all_photos[-1].day if all_photos else None)

    @app.get("/p/<photo_id>")
    def photo_page(photo_id: str):
        p = photos.get(photo_id)
        if p is None:
            abort(404)
        return render_template("photo.html", p=p)

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html")

    # Files ------------------------------------------------------------------------------------
    def _send(path: Path, download_name: str | None = None):
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/jpeg", conditional=True, etag=True, max_age=ONE_YEAR,
                         as_attachment=download_name is not None, download_name=download_name)

    @app.get("/photos/<name>")
    def photo_file(name: str):
        if not name.endswith(".jpg") or not store.valid_id(name[:-4]):
            abort(404)
        pid = name[:-4]
        dl = f"inkstant_{pid}.jpg" if request.args.get("dl") == "1" else None
        return _send(photos.photo_path(pid), dl)

    @app.get("/thumbs/<name>")
    def thumb_file(name: str):
        if not name.endswith(".jpg") or not store.valid_id(name[:-4]):
            abort(404)
        return _send(photos.thumb_path(name[:-4]))

    @app.get("/prints/<name>")
    def print_file(name: str):
        """The photo as the camera's display shows it."""
        if not name.endswith(".png") or not store.valid_id(name[:-4]):
            abort(404)
        pid = name[:-4]
        path = photos.print_path(pid)
        if not path.is_file():
            abort(404)
        dl = f"inkstant_{pid}_print.png" if request.args.get("dl") == "1" else None
        return send_file(path, mimetype="image/png", conditional=True, etag=True,
                         as_attachment=dl is not None, download_name=dl)

    # API ---------------------------------------------------------------------------------------
    @app.get("/api/photos")
    def api_photos():
        try:
            before = int(request.args.get("before", "0")) or None
            after = int(request.args.get("after", "0")) or None
            limit = max(1, min(int(request.args.get("limit", PAGE)), 200))
        except ValueError:
            return api_error("invalid", "Bad page.")
        month = request.args.get("month")
        all_photos = photos.list()
        if after is not None:  # newer photos: the `limit` closest to `after`, newest first
            newer = [p for p in all_photos if p.seq > after][-limit:]
            return jsonify({"photos": [photo_json(p) for p in newer], "next_before": None, "total": len(all_photos)})
        sel = [p for p in all_photos if before is None or p.seq < before]
        if month:
            sel = [p for p in sel if (p.day or "")[:7] <= month] if month != "none" else [p for p in sel if not p.day]
        page = sel[:limit]
        return jsonify({"photos": [photo_json(p) for p in page],
                        "next_before": page[-1].seq if len(sel) > limit else None,
                        "total": len(all_photos)})

    @app.get("/api/photos/<photo_id>")
    def api_photo(photo_id: str):
        p = photos.get(photo_id)
        if p is None:
            return api_error("not_found", "This photo was deleted.")
        from inkstant.imaging.exifinfo import exif_info

        status, _ = call_or_error("status")
        out = photo_json(p) | {"width": 4608, "height": 2592} | exif_info(photos.photo_path(photo_id))
        out["current"] = bool(status and status.get("current") == photo_id)
        out["undeveloped"] = bool(status and status.get("undeveloped") == photo_id)
        return jsonify(out)

    @app.get("/api/months")
    def api_months():
        months: dict[str | None, dict] = {}
        for p in photos.list():  # newest first
            key = p.day[:7] if p.day else None
            m = months.setdefault(key, {"month": key, "count": 0, "first_seq": p.seq})
            m["count"] += 1
        return jsonify(list(months.values()))

    @app.get("/api/status")
    def api_status():
        result, err = call_or_error("status")
        return err or jsonify(result)

    @app.post("/api/photos/delete")
    def api_delete():
        body = request.get_json(silent=True) or {}
        if body.get("all") is True:
            result, err = call_or_error("photos.delete", {"all": True})
        else:
            ids = body.get("ids")
            if not isinstance(ids, list) or not ids or not all(store.valid_id(i) for i in ids):
                return api_error("invalid", "Choose some photos to delete.")
            result, err = call_or_error("photos.delete", {"ids": ids})
        return err or jsonify(result)

    @app.post("/api/photos/<photo_id>/show")
    def api_show(photo_id: str):
        if not store.valid_id(photo_id):
            return api_error("not_found", "That photo isn't on the camera any more.")
        result, err = call_or_error("photos.show", {"id": photo_id})
        if err:
            return err
        return jsonify(result), 202

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        if request.method == "GET":
            result, err = call_or_error("settings.get")
            return err or jsonify(result)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return api_error("invalid", "Expected settings.")
        result, err = call_or_error("settings.set", body)
        return err or jsonify(result)

    @app.post("/api/time")
    def api_time():
        body = request.get_json(silent=True) or {}
        result, err = call_or_error("time.set", {"epoch_ms": body.get("epoch_ms"), "tz": body.get("tz")})
        return err or ("", 204)

    @app.get("/api/wifi")
    def api_wifi():
        result, err = call_or_error("wifi.list")
        return err or jsonify(result)

    @app.post("/api/wifi/home")
    def api_wifi_add():
        body = request.get_json(silent=True) or {}
        result, err = call_or_error("wifi.home.set", {"ssid": body.get("ssid", ""), "password": body.get("password")})
        return err or jsonify(result)

    @app.delete("/api/wifi/home/<int:n>")
    def api_wifi_forget(n: int):
        result, err = call_or_error("wifi.home.remove", {"n": n})
        return err or ("", 204)

    @app.post("/api/share/stop")
    def api_share_stop():
        result, err = call_or_error("share.stop")
        return err or ("", 204)

    @app.route("/api/update", methods=["GET", "POST"])
    def api_update():
        if request.method == "GET":
            result, err = call_or_error("update.check")
            return err or jsonify(result)
        body = request.get_json(silent=True) or {}
        result, err = call_or_error("update.start", {"version": body.get("version")})
        return err or jsonify(result)

    @app.route("/api/selftest", methods=["GET", "POST"])
    def api_selftest():
        result, err = call_or_error("selftest.get" if request.method == "GET" else "selftest.run")
        return err or jsonify(result)

    @app.get("/api/logs")
    def api_logs():
        out = b""
        for boot in ("-1", "0"):
            try:
                p = subprocess.run(["journalctl", "-b", boot, "--no-pager", "-o", "short-iso"],
                                   capture_output=True, timeout=20)
                out += p.stdout
            except (OSError, subprocess.TimeoutExpired):
                pass
        out = out[-2_000_000:] or b"No logs available.\n"
        name = f"inkstant-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        return Response(out, mimetype="text/plain", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/healthz")
    def healthz():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(404)
        return "ok"

    # ZIP downloads -------------------------------------------------------------------------
    def zip_response(chosen: list[store.Photo], filename: str):
        files = [(f"inkstant_{p.id}.jpg", photos.photo_path(p.id), p.when or datetime.now()) for p in chosen]
        plan = zipstream.plan(files)

        def generate():
            last = time.monotonic()
            try:
                for chunk in plan.stream():
                    yield chunk
                    if time.monotonic() - last >= ZIP_ACTIVITY_EVERY:
                        last = time.monotonic()
                        report_activity(force=True)
            except zipstream.FileChanged:
                return  # ends early; the phone reports a failed download (accepted)

        resp = Response(generate(), mimetype="application/zip", direct_passthrough=True)
        resp.headers["Content-Length"] = str(plan.total)
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    @app.route("/download.zip", methods=["GET", "POST"])
    def download_zip():
        all_photos = photos.list()
        today = datetime.now().strftime("%Y-%m-%d")
        if request.method == "GET":
            if request.args.get("set") != "all":
                abort(400)
            return zip_response(list(reversed(all_photos)), f"inkstant_all_{len(all_photos)}-photos.zip")
        if request.form.get("set") == "all":
            return zip_response(list(reversed(all_photos)), f"inkstant_all_{len(all_photos)}-photos.zip")
        day = request.form.get("day")
        if day:
            chosen = [p for p in all_photos if (p.day or "unknown") == day]
        else:
            raw = request.form.get("ids", "")
            wanted = {i for i in raw.replace(",", " ").split() if store.valid_id(i)}
            chosen = [p for p in all_photos if p.id in wanted]
        if not chosen:
            abort(400)
        chosen.reverse()
        return zip_response(chosen, f"inkstant_{len(chosen)}-photos_{today}.zip")

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return api_error("not_found", "Not found.", 404)
        return render_template("error.html", title="Not found", message="That page isn't on the camera."), 404

    @app.errorhandler(421)
    def misdirected(e):
        return Response("Misdirected request\n", status=421, mimetype="text/plain")

    @app.errorhandler(413)
    def too_big(e):
        return api_error("invalid", "Request too large.", 413)

    return app


def serve(paths: Paths | None = None, listen: str = "0.0.0.0:80") -> None:
    """Run under Waitress: 4 threads and small buffers, sized for a Pi 3A+."""
    from waitress import serve as waitress_serve

    app = create_app(paths)
    waitress_serve(app, listen=listen, threads=4, connection_limit=24, channel_timeout=120,
                   outbuf_high_watermark=4 * 1024 * 1024, outbuf_overflow=1024 * 1024,
                   expose_tracebacks=False, ident="inkstant")
