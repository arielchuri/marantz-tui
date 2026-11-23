"""Offline unit tests for marantz-tui (no network required)."""

from __future__ import annotations

import curses
import tempfile
import unittest
from pathlib import Path

from marantz_tui.client import (
    MarantzClient,
    MarantzState,
    format_volume_cmd,
    load_config,
    parse_main_xml,
    parse_telnet_response,
    parse_zone2_xml,
    save_config,
)
from marantz_tui.tui import FRAME_W, MarantzTUI

SAMPLE_MAIN_XML = """<?xml version="1.0" encoding="utf-8" ?>
<item>
<FriendlyName><value>Marantz NR1606</value></FriendlyName>
<Power><value>ON</value></Power>
<ZonePower><value>OFF</value></ZonePower>
<RenameZone><value>Living Rm </value></RenameZone>
<ECOMode><value>AUTO</value></ECOMode>
<InputFuncSelect><value>Online Music</value></InputFuncSelect>
<selectSurround><value>Stereo                         </value></selectSurround>
<MasterVolume><value>-39.5</value></MasterVolume>
<Mute><value>off</value></Mute>
<SleepOff><value>Off</value></SleepOff>
</item>
"""

SAMPLE_Z2_XML = """<?xml version="1.0" encoding="utf-8" ?>
<item>
<Zone><value>Office    </value></Zone>
<Power><value>ON</value></Power>
<InputFuncList>
<value>CBL/SAT</value>
<value>DVD</value>
<value>Blu-ray</value>
<value>NETWORK</value>
</InputFuncList>
<RenameSource>
<value><value>PHONO       </value></value>
<value><value>SYNTH       </value></value>
<value><value>Blu-ray     </value></value>
<value><value>Online Music</value></value>
</RenameSource>
<SourceDelete>
<value>USE</value>
<value>USE</value>
<value>DEL</value>
<value></value>
</SourceDelete>
<InputFuncSelect><value>AirPlay</value></InputFuncSelect>
<MasterVolume><value>-35</value></MasterVolume>
<Mute><value>off</value></Mute>
</item>
"""

SAMPLE_TELNET = (
    "MSSTEREO\rSDAUTO\rPSDRC OFF\rPSLFE 00\rPSBAS 52\rPSTRE 48\r"
    "PSTONE CTRL ON\rPSDYNEQ OFF\rPSMULTEQ:AUDYSSEY\r"
    "NSE0Now Playing\rNSE1Blue in Green\rNSE2Miles Davis\r"
    "NSE3 44.1kHz\rNSE4Kind of Blue\rNSE5   1:24   45%\r"
)


class FakeScr:
    def __init__(self, h: int = 38, w: int = 72) -> None:
        self.h = h
        self.w = w
        self.grid = [[" "] * w for _ in range(h)]

    def erase(self) -> None:
        self.grid = [[" "] * self.w for _ in range(self.h)]

    def getmaxyx(self) -> tuple[int, int]:
        return (self.h, self.w)

    def addstr(self, y: int, x: int, s: str, attr: int = 0) -> None:
        for i, ch in enumerate(s):
            if 0 <= y < self.h and 0 <= x + i < self.w:
                self.grid[y][x + i] = ch

    def refresh(self) -> None:
        pass


class TestMarantzTUI(unittest.TestCase):
    def test_parse_main_and_zone2_xml(self) -> None:
        st = MarantzState(host="192.0.2.10")
        parse_main_xml(SAMPLE_MAIN_XML, st)
        parse_zone2_xml(SAMPLE_Z2_XML, st)

        self.assertEqual(st.model, "Marantz NR1606")
        self.assertEqual(st.main.name, "Living Rm")
        self.assertEqual(st.main.power, "OFF")
        self.assertEqual(st.main.volume_db, -39.5)
        self.assertEqual(st.surround, "Stereo")
        self.assertEqual(st.eco, "AUTO")

        self.assertTrue(st.has_z2)
        self.assertEqual(st.z2.name, "Office")
        self.assertEqual(st.z2.power, "ON")
        self.assertEqual(st.z2.volume_db, -35.0)
        # Deleted Blu-ray source should be filtered out
        self.assertEqual(
            st.inputs,
            [("PHONO", "SAT/CBL"), ("SYNTH", "DVD"), ("Online Music", "NET")],
        )

    def test_parse_telnet_response(self) -> None:
        au = parse_telnet_response(SAMPLE_TELNET)
        self.assertEqual(au.signal_mode, "AUTO")
        self.assertEqual(au.bass_db, "+2dB")
        self.assertEqual(au.treble_db, "-2dB")
        self.assertEqual(au.tone_ctrl, "ON")
        self.assertEqual(au.multeq, "AUDYSSEY")
        self.assertEqual(au.dyneq, "OFF")
        self.assertEqual(au.drc, "OFF")
        self.assertEqual(au.lfe_db, "0dB")
        self.assertEqual(au.track, "Blue in Green")
        self.assertEqual(au.artist, "Miles Davis")
        self.assertEqual(au.sample_rate, "44.1kHz")
        self.assertEqual(au.elapsed, "1:24")

    def test_volume_formatting_and_clamping(self) -> None:
        self.assertEqual(format_volume_cmd("main", -40.0), "MV40")
        self.assertEqual(format_volume_cmd("main", -39.5), "MV405")
        self.assertEqual(format_volume_cmd("main", 15.0), "MV80")
        self.assertEqual(format_volume_cmd("main", -95.0), "MV00")
        self.assertEqual(format_volume_cmd("z2", -40.0), "Z240")
        self.assertEqual(format_volume_cmd("z2", -35.4), "Z245")

    def test_config_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "config.json"
            save_config({"host": "192.0.2.55", "preset_z2_vol": -42.0}, cfg_file)
            loaded = load_config(cfg_file)
            self.assertEqual(loaded["host"], "192.0.2.55")
            self.assertEqual(loaded["preset_z2_vol"], -42.0)

    def test_tui_render_borders_stay_within_frame(self) -> None:
        orig_color_pair = curses.color_pair
        curses.color_pair = lambda n: 0
        try:
            client = MarantzClient("192.0.2.10")
            tui = MarantzTUI(client)
            st = MarantzState(host="192.0.2.10", online=True)
            parse_main_xml(SAMPLE_MAIN_XML, st)
            parse_zone2_xml(SAMPLE_Z2_XML, st)
            st.audio = parse_telnet_response(SAMPLE_TELNET)
            tui.state = st
            scr = FakeScr(38, 72)
            tui._draw(scr)
            non_empty = ["".join(row).rstrip() for row in scr.grid if "".join(row).strip()]
            self.assertGreater(len(non_empty), 30)
            for line in non_empty:
                stripped = line.lstrip()
                self.assertEqual(len(stripped), FRAME_W, f"Row width mismatch: {stripped!r}")
        finally:
            curses.color_pair = orig_color_pair


if __name__ == "__main__":
    unittest.main()
