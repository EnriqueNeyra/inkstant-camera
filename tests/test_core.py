import asyncio
import json
import random
from datetime import datetime

import pytest

from inkstant import atomic, device, ipc, settings, state, store


# atomic ---------------------------------------------------------------------
def test_atomic_write_and_modes(paths):
    target = paths.var / "x.bin"
    atomic.atomic_write(target, b"hello", mode=0o640, tmp_dir=paths.tmp)
    assert target.read_bytes() == b"hello"
    assert (target.stat().st_mode & 0o777) == 0o640
    assert list(paths.tmp.iterdir()) == []


def test_write_json_keeps_backup_and_falls_back(paths):
    f = paths.var / "s.json"
    atomic.write_json(f, {"a": 1})
    atomic.write_json(f, {"a": 2})
    assert json.loads((paths.var / "s.json.bak").read_text()) == {"a": 1}
    f.write_text("{broken")
    value, err = atomic.read_json(f)
    assert value == {"a": 1} and "s.json" in err


# settings -------------------------------------------------------------------
def test_settings_defaults_valid():
    assert settings.validate(settings.DEFAULTS) == settings.DEFAULTS


def test_settings_merge_partial_and_errors():
    s = settings.validate(settings.DEFAULTS)
    s2 = settings.merge(s, {"slideshow": {"interval_min": 15}, "sleep_min": None})
    assert s2["slideshow"]["interval_min"] == 15 and s2["slideshow"]["enabled"] is True
    assert s2["sleep_min"] is None
    with pytest.raises(settings.SettingsError) as e:
        settings.merge(s, {"auto_off_min": 0})
    assert e.value.field == "auto_off_min"
    with pytest.raises(settings.SettingsError):
        settings.merge(s, {"low_battery_shutdown_pct": 3})
    with pytest.raises(settings.SettingsError):
        settings.merge(s, {"nonsense": 1})
    with pytest.raises(settings.SettingsError):
        settings.merge(s, {"slideshow": {"quiet_hours": {"start": "23:00", "end": "23:00"}}})
    ok = settings.merge(s, {"slideshow": {"quiet_hours": {"start": "23:00", "end": "07:00"}}})
    assert ok["slideshow"]["quiet_hours"] == {"start": "23:00", "end": "07:00"}
    with pytest.raises(settings.SettingsError):
        settings.merge(s, {"hotspot": {"password": "short"}})
    with pytest.raises(settings.SettingsError):
        settings.merge(s, {"hotspot": {"ssid": "x" * 33}})
    assert settings.merge(s, {"hotspot": {"ssid": "Café"}})["hotspot"]["ssid"] == "Café"
    with pytest.raises(settings.SettingsError):
        settings.merge(s, {"auto_off_min": True})


def test_settings_load_repairs_bad_field(paths):
    raw = dict(settings.DEFAULTS, auto_off_min=999)
    paths.settings.write_text(json.dumps(raw))
    s = settings.load(paths.settings)
    assert s["auto_off_min"] == 10


# state ----------------------------------------------------------------------
def test_state_roundtrip(paths):
    s = state.State(next_seq=7, undeveloped="000006", saver_auto_active=True)
    state.save_state(paths.state, s, tmp_dir=paths.tmp)
    assert state.load_state(paths.state) == s


def test_state_ignores_bad_values():
    s = state.State.from_dict({"next_seq": -3, "display_content": "nope", "next_wake_utc": True})
    assert s.next_seq == 1 and s.display_content == "unknown" and s.next_wake_utc is None


def test_shuffle_bag_shows_all_before_repeating():
    s = state.SlideshowState()
    rng = random.Random(1)
    avail = list(range(1, 11))
    first = [s.next(avail, "shuffle", rng) for _ in range(10)]
    assert sorted(first) == avail
    second = s.next(avail, "shuffle", rng)
    assert second != first[-1]


def test_shuffle_bag_skips_deleted_and_adds_new():
    s = state.SlideshowState(bag=[1, 2, 3], last=None)
    assert s.next([2, 3], "shuffle", random.Random(0)) in (2, 3)
    s.add(9, random.Random(0))
    assert 9 in s.bag


def test_newest_order_wraps():
    s = state.SlideshowState()
    seen = [s.next([1, 2, 3], "newest") for _ in range(4)]
    assert seen == [3, 2, 1, 3]


# store ----------------------------------------------------------------------
def test_ids():
    assert store.make_id(123, datetime(2026, 10, 15, 14, 30, 22)) == "000123_20261015-143022"
    assert store.make_id(5, None) == "000005"
    assert store.valid_id("000123_20261015-143022") and store.valid_id("000123")
    for bad in ("../etc/passwd", "12345", "000123_2026", "000123.jpg", 123, None):
        assert not store.valid_id(bad)
    assert store.when_of("000123_20261015-143022") == datetime(2026, 10, 15, 14, 30, 22)
    assert store.when_of("000123") is None


def test_commit_list_delete_and_recover(paths):
    st = store.PhotoStore(paths, web_group=None)
    st.commit("000001", b"E", b"T", b"J1")
    st.commit("000002_20261015-120000", b"E", b"T", b"J22")
    photos = st.list()
    assert [p.seq for p in photos] == [2, 1]
    assert photos[0].day == "2026-10-15" and photos[0].time == "12:00"
    assert st.max_seq() == 2
    # Orphans and leftovers from a power cut.
    (paths.epd / "000009.epd").write_bytes(b"x")
    (paths.tmp / "junk.tmp").write_bytes(b"x")
    (paths.thumbs / "000001.jpg").unlink()
    rebuild = st.recover()
    assert rebuild == ["000001"]
    assert not (paths.epd / "000009.epd").exists() and list(paths.tmp.iterdir()) == []
    deleted, missing = st.delete(["000001", "000077", "../x"])
    assert deleted == ["000001"] and missing == ["000077", "../x"]
    assert not (paths.epd / "000001.epd").exists()


def test_storage_thresholds(paths):
    st = store.PhotoStore(paths, web_group=None)
    assert st.storage_state(free=2 << 30) == "ok"
    assert st.storage_state(free=900 << 20) == "low"
    assert st.storage_state(free=100 << 20) == "full"
    assert st.photos_left(free=(500 << 20) + 8_000_000) == 2


# device ---------------------------------------------------------------------
def test_device_roundtrip(tmp_path):
    d = device.Device(model='Raspberry Pi 3 Model A Plus Rev 1.1', family="pi3", wifi_5ghz=True,
                      camera_hflip=True, camera_vflip=True, display_rotation=180, camera_port="cam1")
    f = tmp_path / "device.toml"
    f.write_text(device.dumps(d))
    assert device.load(f) == d


def test_device_bad_values_fall_back(tmp_path):
    f = tmp_path / "device.toml"
    f.write_text('display_rotation = 90\ncamera_fps = 99\nwifi_5ghz = "yes"\n')
    d = device.load(f)
    assert d.display_rotation == 0 and d.camera_fps is None and d.wifi_5ghz is True
    assert len(d.errors) == 3


def test_family_from_model():
    assert device.family_from_model("Raspberry Pi 3 Model A Plus Rev 1.1") == "pi3"
    assert device.family_from_model("Raspberry Pi 3 Model B Rev 1.2") == "pi3"
    assert device.family_from_model("Raspberry Pi 4 Model B Rev 1.5") == "pi4"
    assert device.family_from_model("Raspberry Pi 5 Model B Rev 1.0") == "pi5"
    assert device.family_from_model("Raspberry Pi Zero 2 W Rev 1.0") is None
    assert device.family_from_model("Raspberry Pi 400 Rev 1.0") is None


# ipc ------------------------------------------------------------------------
def test_ipc_roundtrip_and_roles(paths):
    async def handler(cmd, args, role):
        if cmd == "status":
            return {"role": role, "echo": args}
        raise ipc.IpcError("not_found", "Nope.")

    async def main():
        server = ipc.ControlServer(paths.control_sock, handler, trust_all=True)
        await server.start()
        loop = asyncio.get_running_loop()
        try:
            r = await loop.run_in_executor(None, ipc.call, paths.control_sock, "status", {"x": 1})
            assert r == {"role": "root", "echo": {"x": 1}}
            with pytest.raises(ipc.IpcError) as e:
                await loop.run_in_executor(None, ipc.call, paths.control_sock, "photos.show", {})
            assert e.value.code == "not_found"
            with pytest.raises(ipc.IpcError) as e:
                await loop.run_in_executor(None, ipc.call, paths.control_sock, "rm -rf", {})
            assert e.value.code == "unknown"
            server.trust_all = False
            # Non-Linux test machines can't read peer credentials, so the caller is unknown.
            if ipc.peer_uid.__module__ and not __import__("sys").platform.startswith("linux"):
                with pytest.raises(ipc.IpcError) as e:
                    await loop.run_in_executor(None, ipc.call, paths.control_sock, "status", {})
                assert e.value.code == "denied"
        finally:
            await server.close()

    asyncio.run(main())


def test_ipc_role_rules(paths):
    server = ipc.ControlServer(paths.control_sock, None)
    server.web_uid = 999
    assert server.role_for(0) == "root"
    assert server.role_for(999) == "gallery"
    assert server.role_for(1000) is None
    assert server.role_for(None) is None
    assert "power.button" not in ipc.GALLERY_COMMANDS and "status" in ipc.GALLERY_COMMANDS


def test_ipc_unavailable(paths):
    with pytest.raises(ipc.IpcError) as e:
        ipc.call(paths.control_sock, "status")
    assert e.value.code == "unavailable"
