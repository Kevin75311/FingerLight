"""What the strip sees between frames, and who may drive it.  Found on the Pi
(2026-11-02): frames sent close together with 60 us of low between them garbled the bar
-- LED 0 flashing green, random colours -- so these pin the wire-level guarantees."""

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HW = os.path.join(os.path.dirname(HERE), "hw")
sys.path.insert(0, HW)

import ws2812_spi as W  # noqa: E402


def low_us(nbytes, hz=W.SPI_HZ):
    return nbytes * 8 / hz * 1e6


def test_every_frame_is_wrapped_in_a_latch_length_low():
    s = W.Strip(176)
    s[0] = (255, 255, 255)
    f = s.frame()
    lead = len(s._reset)
    assert low_us(lead) >= 280, "WS2812B-V5 needs 280 us of low to latch"
    # low on both sides, so any two frames, however close, have 2 x 300 us between them
    assert f[:lead] == bytes(lead) and f[-lead:] == bytes(lead)
    assert f[lead] != 0, "the pixel data starts right after the lead-in"
    assert len(f) <= 4096, "one spidev transfer at the stock bufsiz"


def test_identical_frames_are_not_resent_but_changes_and_force_are():
    s = W.Strip(10).open(dry=True)
    s[3] = (10, 20, 30)
    assert s.show() > 0
    assert s.show() == 0                    # same bytes: the strip already shows them
    s.brightness = 0.5
    assert s.show() > 0                     # brightness changes the bytes
    assert s.show(force=True) > 0
    assert s.spi.frames == 3
    s.close()
    s.open(dry=True)
    assert s.show() > 0, "a re-opened strip must not assume what it shows"


def test_a_second_process_cannot_take_the_strip(tmp_path):
    path = str(tmp_path / "spi.lock")
    held = W.claim_strip(path)
    code = ("import sys; sys.path.insert(0, %r); import ws2812_spi as W\n"
            "try:\n    W.claim_strip(%r)\nexcept RuntimeError as e:\n"
            "    print(e); sys.exit(3)\n" % (HW, path))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 3
    assert "already in use by pid %d" % os.getpid() in r.stdout
    assert "systemctl stop fingerlight" in r.stdout
    held.close()                            # released: now the other process may
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0


def test_the_lock_dies_with_its_holder(tmp_path):
    path = str(tmp_path / "spi.lock")
    subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, %r); import ws2812_spi as W; "
                    "W.claim_strip(%r)" % (HW, path)], check=True)
    W.claim_strip(path).close()             # the exited process left no stale lock


@pytest.mark.parametrize("bus,device", [(0, 0), (1, 2)])
def test_lock_path_names_the_device(bus, device):
    assert W.lock_path(bus, device).endswith("fingerlight-spi%d.%d.lock" % (bus, device))
