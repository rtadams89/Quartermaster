"""The Pi camera helper (built into pi/install.sh), run against a fake camera program."""
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parent.parent / "pi" / "install.sh"

FAKE = textwrap.dedent("""
    import io, sys, time
    from PIL import Image
    n = 0
    while True:
        n += 1
        o = io.BytesIO(); Image.new("RGB", (320, 240), (n % 255, 90, 90)).save(o, "JPEG")
        sys.stdout.buffer.write(o.getvalue()); sys.stdout.buffer.flush(); time.sleep(0.03)
""")


@pytest.fixture()
def helper(tmp_path):
    script = tmp_path / "helper.py"
    script.write_text(subprocess.run(["bash", str(INSTALL), "--print-helper"], capture_output=True, text=True, check=True).stdout)
    fake = tmp_path / "fakevid.py"
    fake.write_text(FAKE)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, QM_CAMERA_PORT=str(port), QM_CAMERA_COMMAND=f"{sys.executable} {fake}")
    proc = subprocess.Popen([sys.executable, str(script)], env=env, stdout=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/health", timeout=0.5)
            break
        except OSError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(5)


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read(), r.headers


def test_health_reports_camera(helper):
    assert json.loads(_get(helper + "/health")[0]) == {"ok": True, "camera": True}


def test_frames_are_jpegs_and_the_counter_moves_on(helper):
    a, ha = _get(helper + "/frame.jpg")
    seq = int(ha["X-Frame"])
    b, hb = _get(helper + f"/frame.jpg?after={seq}")
    assert a[:3] == b[:3] == b"\xff\xd8\xff" and a[-2:] == b[-2:] == b"\xff\xd9"
    assert int(hb["X-Frame"]) > seq


def test_snapshot_path_is_gone(helper):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(helper + "/snapshot.jpg")
    assert e.value.code == 404


def test_unknown_path_is_404(helper):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(helper + "/nope")
    assert e.value.code == 404


# ------------------------------------------------------------------ autofocus
def _load_helper(tmp_path, monkeypatch, **env):
    import importlib.util
    monkeypatch.delenv("QM_CAMERA_COMMAND", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    src = tmp_path / "helper_mod.py"
    src.write_text(subprocess.run(["bash", str(INSTALL), "--print-helper"], capture_output=True, text=True, check=True).stdout)
    spec = importlib.util.spec_from_file_location("helper_mod", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.VIDEO = ["rpicam-vid"]
    return mod


def _listing(mod, monkeypatch, text):
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=text, stderr=""))


def test_autofocus_is_on_for_camera_module_3(tmp_path, monkeypatch):
    mod = _load_helper(tmp_path, monkeypatch)
    _listing(mod, monkeypatch, "Available cameras\n0 : imx708 [4608x2592] (/base/axi/pcie@120000/rp1/i2c@80000/imx708@1a)")
    argv = mod.Camera()._argv()
    assert argv[argv.index("--autofocus-mode") + 1] == "continuous"
    assert argv[argv.index("--autofocus-range") + 1] == "full"


def test_fixed_focus_cameras_get_no_autofocus_options(tmp_path, monkeypatch):
    mod = _load_helper(tmp_path, monkeypatch)
    _listing(mod, monkeypatch, "Available cameras\n0 : ov5647 [2592x1944]")
    assert "--autofocus-mode" not in mod.Camera()._argv()


def test_autofocus_setting_can_force_or_disable(tmp_path, monkeypatch):
    mod = _load_helper(tmp_path, monkeypatch, QM_CAMERA_AUTOFOCUS="continuous")
    assert "--autofocus-mode" in mod.Camera()._argv()
    mod = _load_helper(tmp_path, monkeypatch, QM_CAMERA_AUTOFOCUS="off")
    _listing(mod, monkeypatch, "0 : imx708")
    assert "--autofocus-mode" not in mod.Camera()._argv()


def test_a_failed_camera_listing_means_no_autofocus_options(tmp_path, monkeypatch):
    mod = _load_helper(tmp_path, monkeypatch)
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert "--autofocus-mode" not in mod.Camera()._argv()
