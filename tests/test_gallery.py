import io
import zipfile

import pytest

from inkstant import ipc, store
from inkstant.gallery.app import CSP, create_app


@pytest.fixture
def gallery(paths):
    st = store.PhotoStore(paths, web_group=None)
    ids = ["000001_20261014-120000", "000002_20261015-090000", "000003_20261015-100000", "000004"]
    for pid in ids:
        st.commit(pid, b"E" * 10, b"T" * 10, b"\xff\xd8" + pid.encode() + b"\xff\xd9", b"\x89PNG" + pid.encode())
    calls = []
    status = {"current": ids[2], "undeveloped": None, "module_present": True, "lifecycle": "ACTIVE"}

    def call(cmd, args=None):
        calls.append((cmd, args))
        if cmd == "status":
            return status
        if cmd == "photos.show":
            if args["id"] == "000001_20261014-120000":
                raise ipc.IpcError("no_module", "No display module in the camera.")
            return {"queued": "now"}
        if cmd == "photos.delete":
            return {"deleted": args.get("ids", []), "missing": []}
        if cmd == "settings.set":
            if "auto_off_min" in args and args["auto_off_min"] == 0:
                raise ipc.IpcError("invalid", "auto_off_min: Must be between 1 and 60.")
            return {"settings": {}, "applied": []}
        if cmd == "update.check":
            raise ipc.IpcError("offline", "Needs internet. Share at home to update.")
        return {}

    app = create_app(paths, hostname="inkstant", call=call)
    app.testing = True
    client = app.test_client()
    return client, calls, ids


H = {"Host": "10.42.0.1"}
POST = {"Host": "10.42.0.1", "X-Inkstant": "1"}


def test_gallery_page_and_headers(gallery):
    c, calls, ids = gallery
    r = c.get("/", headers=H)
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == CSP
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Referrer-Policy"] == "no-referrer"
    html = r.get_data(as_text=True)
    for pid in ids:
        assert f'data-id="{pid}"' in html
    assert "<script>" not in html and "style=" not in html  # CSP: no inline scripts or styles
    assert any(c[0] == "share.activity" for c in calls)  # a page load counts as use


def test_host_check(gallery):
    c, _, _ = gallery
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 421
    assert c.get("/", headers={"Host": "inkstant.local"}).status_code == 200
    assert c.get("/", headers={"Host": "192.168.1.23:80"}).status_code == 200
    assert c.get("/", headers={"Host": "localhost:8080"}).status_code == 200


def test_status_polls_dont_count_as_use(gallery):
    c, calls, _ = gallery
    calls.clear()
    c.get("/api/status", headers={**H, "X-Inkstant-Poll": "1"})
    assert not any(c[0] == "share.activity" for c in calls)


def test_photo_files_and_caching(gallery):
    c, _, ids = gallery
    r = c.get(f"/photos/{ids[0]}.jpg", headers=H)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert "immutable" in r.headers["Cache-Control"]
    r = c.get(f"/photos/{ids[0]}.jpg?dl=1", headers=H)
    assert f'filename=inkstant_{ids[0]}.jpg' in r.headers["Content-Disposition"]
    assert c.get("/photos/..%2Fsettings.json", headers=H).status_code == 404
    assert c.get("/photos/000099.jpg", headers=H).status_code == 404
    assert c.get(f"/thumbs/{ids[0]}.jpg", headers=H).status_code == 200
    assert c.get("/static/app.css", headers=H).headers["Cache-Control"] == "no-cache"


def test_print_files(gallery):
    c, _, ids = gallery
    r = c.get(f"/prints/{ids[0]}.png", headers=H)
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == b"\x89PNG" + ids[0].encode()
    assert r.headers["Cache-Control"] == "no-cache"  # remade when the palette changes
    r = c.get(f"/prints/{ids[0]}.png?dl=1", headers=H)
    assert f"filename=inkstant_{ids[0]}_print.png" in r.headers["Content-Disposition"]
    assert c.get(f"/prints/{ids[0]}.jpg", headers=H).status_code == 404
    assert c.get("/prints/000099.png", headers=H).status_code == 404
    # The grid shows only the photos: one tile each, never a separate tile for the print.
    page = c.get("/", headers=H).get_data(as_text=True)
    assert "/prints/" not in page and page.count(f"/thumbs/{ids[0]}.jpg") == 1


def test_api_photos_paging(gallery):
    c, _, ids = gallery
    r = c.get("/api/photos?limit=2", headers=H).get_json()
    assert [p["id"] for p in r["photos"]] == [ids[3], ids[2]] and r["next_before"] == 3 and r["total"] == 4
    assert r["offset"] == 0
    r = c.get("/api/photos?before=3&limit=2", headers=H).get_json()
    assert [p["id"] for p in r["photos"]] == [ids[1], ids[0]] and r["next_before"] is None
    assert r["offset"] == 2  # two newer photos come before this page: the viewer counts from 3
    r = c.get("/api/photos?after=1&limit=2", headers=H).get_json()
    assert [p["id"] for p in r["photos"]] == [ids[2], ids[1]] and r["offset"] == 1
    undated = c.get("/api/photos?limit=1", headers=H).get_json()["photos"][0]
    assert undated["day"] is None and undated["time"] is None
    months = c.get("/api/months", headers=H).get_json()
    assert {m["month"] for m in months} == {None, "2026-10"}


def test_mutations_need_header_and_same_origin(gallery):
    c, _, ids = gallery
    assert c.post("/api/photos/delete", json={"ids": [ids[0]]}, headers=H).status_code == 403
    bad_origin = {**POST, "Origin": "http://evil.example"}
    assert c.post("/api/photos/delete", json={"ids": [ids[0]]}, headers=bad_origin).status_code == 403
    r = c.post("/api/photos/delete", json={"ids": [ids[0]]}, headers={**POST, "Origin": "http://10.42.0.1"})
    assert r.status_code == 200 and r.get_json()["deleted"] == [ids[0]]
    assert c.post("/api/photos/delete", json={"ids": ["../x"]}, headers=POST).status_code == 400


def test_errors_map_to_http(gallery):
    c, _, ids = gallery
    r = c.post(f"/api/photos/{ids[0]}/show", headers=POST)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "no_module"
    assert c.post(f"/api/photos/{ids[2]}/show", headers=POST).status_code == 202
    r = c.post("/api/settings", json={"auto_off_min": 0}, headers=POST)
    assert r.status_code == 400 and "between" in r.get_json()["error"]["message"]
    assert c.get("/api/update", headers=H).status_code == 409


def test_zip_downloads(gallery, paths):
    c, _, ids = gallery
    r = c.get("/download.zip?set=all", headers=H)
    data = r.get_data()
    assert r.headers["Content-Length"] == str(len(data))
    assert "inkstant_all_4-photos.zip" in r.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() == [f"inkstant_{i}.jpg" for i in ids]
    r = c.post("/download.zip", data={"day": "2026-10-15"}, headers=H)
    with zipfile.ZipFile(io.BytesIO(r.get_data())) as z:
        assert z.namelist() == [f"inkstant_{ids[1]}.jpg", f"inkstant_{ids[2]}.jpg"]
    r = c.post("/download.zip", data={"ids": f"{ids[0]},{ids[3]}"}, headers=H)
    assert "inkstant_2-photos_" in r.headers["Content-Disposition"]
    assert c.post("/download.zip", data={"ids": "nope"}, headers=H).status_code == 400


def test_healthz_only_local(gallery):
    c, _, _ = gallery
    assert c.get("/healthz", headers=H, environ_base={"REMOTE_ADDR": "127.0.0.1"}).data == b"ok"
    assert c.get("/healthz", headers=H, environ_base={"REMOTE_ADDR": "10.42.0.55"}).status_code == 404


def test_photo_page_and_404(gallery):
    c, _, ids = gallery
    assert c.get(f"/p/{ids[1]}", headers=H).status_code == 200
    assert c.get("/p/999999", headers=H).status_code == 404
    assert c.get("/settings", headers=H).status_code == 200
    assert c.get("/api/nothing", headers=H).get_json()["error"]["code"] == "not_found"
