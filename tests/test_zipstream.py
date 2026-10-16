import io
import shutil
import subprocess
import zipfile
from datetime import datetime

import pytest

from inkstant.gallery import zipstream


def make_files(tmp_path, n=3):
    out = []
    for i in range(n):
        p = tmp_path / f"{i:06d}.jpg"
        p.write_bytes(bytes([i]) * (1000 + i * 37) + b"\xff\xd9")
        out.append((f"inkstant_{i:06d}.jpg", p, datetime(2026, 10, 15, 14, 30, 22)))
    return out


@pytest.mark.parametrize("threshold", [zipstream.ZIP32_LIMIT, 1000])
def test_zip_reads_back_and_length_is_exact(tmp_path, threshold):
    files = make_files(tmp_path)
    plan = zipstream.plan(files, zip64_threshold=threshold)
    data = b"".join(plan.stream())
    assert len(data) == plan.total
    assert plan.zip64 == (threshold == 1000)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        assert z.namelist() == [f[0] for f in files]
        for name, path, _ in files:
            assert z.read(name) == path.read_bytes()
            info = z.getinfo(name)
            assert info.compress_type == zipfile.ZIP_STORED and info.date_time == (2026, 10, 15, 14, 30, 22)
            assert not info.flag_bits & 0x08  # no data descriptor
    out = tmp_path / "t.zip"
    out.write_bytes(data)
    for tool in (["unzip", "-tq"], ["7z", "t"]):
        if shutil.which(tool[0]):
            r = subprocess.run([*tool, str(out)], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr


def test_many_entries_force_zip64(tmp_path):
    files = make_files(tmp_path, n=5)
    plan = zipstream.plan(files, entry_limit=3)
    data = b"".join(plan.stream())
    assert plan.zip64 and len(data) == plan.total
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len(z.namelist()) == 5 and z.testzip() is None


def test_empty_zip(tmp_path):
    plan = zipstream.plan([])
    data = b"".join(plan.stream())
    assert len(data) == plan.total == 22
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == []


def test_file_deleted_mid_download(tmp_path):
    files = make_files(tmp_path)
    plan = zipstream.plan(files)
    it = plan.stream()
    next(it)
    files[1][1].unlink()
    with pytest.raises(zipstream.FileChanged):
        list(it)
