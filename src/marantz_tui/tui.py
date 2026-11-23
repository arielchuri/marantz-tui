"""Compact 62-column vintage Marantz faceplate animated curses TUI."""

from __future__ import annotations

import curses
import math
import random
import threading
import time
from typing import Optional

from .client import ECO_MODES, SURROUND_MODES, MarantzClient, MarantzState

FRAME_W = 62
VU_CHARS = " ▁▂▃▄▅▆▇█"

ASCII_BANNER = [
    r" __  __   _   ___   _   _  _ _____ ____ ",
    r"|  \/  | /_\ | _ \ /_\ | \| |_   _|_  / ",
    r"| |\/| |/ _ \|   // _ \| .` | | |  / /  ",
    r"|_|  |_/_/ \_\_|_\_/ \_\_|\_| |_| /___| ",
]


def fmt_vol(v: Optional[float]) -> str:
    if v is None:
        return "--.- dB"
    return f"{v:5.1f} dB"


def vol_bar(v: Optional[float], width: int = 26, muted: bool = False) -> str:
    if v is None:
        return "░" * width
    pct = max(0.0, min(1.0, (v + 80.0) / 80.0))
    filled = int(round(pct * width))
    if muted:
        return "▒" * filled + "·" * (width - filled)
    return "█" * filled + "░" * (width - filled)


class MarantzTUI:
    def __init__(
        self,
        client: MarantzClient,
        preset_main_vol: float = -40.0,
        preset_z2_vol: float = -40.0,
    ) -> None:
        self.client = client
        self.preset_main_vol = preset_main_vol
        self.preset_z2_vol = preset_z2_vol
        self.state = MarantzState(host=client.host)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.active_zone = "main"
        self.toast = f"Connecting to {client.host}..."
        self.toast_until = time.time() + 2.5
        self.tick = 0
        self.vu_levels = [0.0] * 18
        self.poll_trigger = threading.Event()

    def set_toast(self, msg: str, duration: float = 2.4) -> None:
        with self.lock:
            self.toast = msg
            self.toast_until = time.time() + duration

    def _poller(self) -> None:
        cycle = 0
        while not self.stop_event.is_set():
            st = self.client.fetch_state()
            with self.lock:
                prev_audio = self.state.audio
            if st.online and cycle % 2 == 0:
                audio = self.client.fetch_audio_telnet(prev_audio)
                if st.audio.stream_source:
                    audio.stream_source = st.audio.stream_source
                st.audio = audio
            else:
                prev_audio.stream_source = st.audio.stream_source or prev_audio.stream_source
                st.audio = prev_audio
            with self.lock:
                self.state = st
                if not st.has_z2 and self.active_zone == "z2":
                    self.active_zone = "main"
            cycle += 1
            self.poll_trigger.wait(timeout=1.6)
            self.poll_trigger.clear()

    def _run_async(self, label: str, fn) -> None:
        self.set_toast(f">> {label}...")

        def worker() -> None:
            try:
                fn()
                self.set_toast(f"OK: {label}")
            except Exception as exc:
                self.set_toast(f"ERR: {exc}", duration=3.5)
            finally:
                time.sleep(0.25)
                self.poll_trigger.set()

        threading.Thread(target=worker, daemon=True).start()

    def _safe_add(self, scr, y: int, x: int, text: str, attr: int = 0) -> None:
        max_y, max_x = scr.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        avail = max_x - x - (1 if y == max_y - 1 else 0)
        if avail <= 0:
            return
        try:
            scr.addstr(y, x, text[:avail], attr)
        except curses.error:
            pass

    def _hline(self, scr, y: int, x0: int, w: int, left: str, fill: str, right: str, attr: int) -> None:
        self._safe_add(scr, y, x0, left + (fill * (w - 2)) + right, attr)

    def _frame_row(self, scr, y: int, x0: int, w: int, border_attr: int) -> None:
        self._safe_add(scr, y, x0, "║", border_attr)
        self._safe_add(scr, y, x0 + w - 1, "║", border_attr)

    def _put(self, scr, y: int, x0: int, w: int, col: int, text: str, attr: int = 0) -> None:
        inner = w - 2
        if col < 0 or col >= inner:
            return
        self._safe_add(scr, y, x0 + 1 + col, text[: inner - col], attr)

    def _update_vu(self, st: MarantzState) -> None:
        z = st.main if self.active_zone == "main" else st.z2
        playing = st.online and z.power == "ON" and not z.mute
        base_energy = 0.0
        if playing and z.volume_db is not None:
            base_energy = max(0.18, min(1.0, (z.volume_db + 75.0) / 55.0))
        elif playing:
            base_energy = 0.45

        for i in range(len(self.vu_levels)):
            if playing:
                wave = (
                    0.45
                    + 0.30 * math.sin(self.tick * 0.38 + i * 0.55)
                    + 0.25 * math.cos(self.tick * 0.21 - i * 0.85)
                )
                target = base_energy * max(0.08, min(1.0, wave + random.uniform(-0.18, 0.20)))
            else:
                target = 0.0
            self.vu_levels[i] = self.vu_levels[i] * 0.45 + target * 0.55

    def _draw(self, scr) -> None:
        scr.erase()
        max_y, max_x = scr.getmaxyx()
        w = min(FRAME_W, max_x - 2)
        if w < 52 or max_y < 26:
            self._safe_add(scr, 1, 2, f"Terminal too small ({max_x}x{max_y}). Need >= 54x26.", curses.A_BOLD)
            scr.refresh()
            return

        x0 = max(0, (max_x - w) // 2)
        inner = w - 2

        with self.lock:
            st = self.state
            toast = self.toast if time.time() < self.toast_until else ""

        self._update_vu(st)

        c_gold = curses.color_pair(1) | curses.A_BOLD
        c_cyan = curses.color_pair(2) | curses.A_BOLD
        c_green = curses.color_pair(3) | curses.A_BOLD
        c_dim = curses.color_pair(4)
        c_red = curses.color_pair(5) | curses.A_BOLD
        c_inv = curses.color_pair(6) | curses.A_BOLD
        c_white = curses.A_BOLD

        y = 0
        self._hline(scr, y, x0, w, "╔", "=", "╗", c_gold)
        y += 1

        # Animated ASCII art headline
        sweep = (self.tick // 2) % 44
        for line in ASCII_BANNER:
            self._frame_row(scr, y, x0, w, c_gold)
            pad_left = max(0, (inner - len(line)) // 2)
            for idx, ch in enumerate(line):
                if ch != " ":
                    attr = c_cyan if abs(idx - sweep) <= 2 else c_gold
                    self._put(scr, y, x0, w, pad_left + idx, ch, attr)
            y += 1

        # Subtitle + animated link LED
        pulse_led = "(*)" if (self.tick // 4) % 2 == 0 else "(o)"
        link_txt = f"{pulse_led} {st.host}" if st.online else "( ) OFFLINE"
        link_attr = c_green if st.online else c_red
        sub_left = f"  {st.model[:24]}  |  ECO: {st.eco}"
        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 0, sub_left, c_dim)
        self._put(scr, y, x0, w, inner - len(link_txt) - 2, link_txt, link_attr)
        y += 1

        self._hline(scr, y, x0, w, "╠", "=", "╣", c_gold)
        y += 1

        # Zone cards
        zones = [("main", st.main, "1")]
        if st.has_z2:
            zones.append(("z2", st.z2, "2"))

        for zidx, (zkey, zobj, hotkey) in enumerate(zones):
            selected = self.active_zone == zkey
            is_on = zobj.power == "ON"
            led = ("(*)" if (self.tick // 5) % 2 == 0 else "(o)") if is_on else "( )"
            badge = f"[{hotkey}] {zobj.name.upper()[:11]}"
            sel_mark = "<< ACTIVE" if selected else ""

            self._frame_row(scr, y, x0, w, c_gold)
            self._put(scr, y, x0, w, 2, f" {badge:<15} ", c_inv if selected else c_white)
            pwr_str = f"{led} {'ON ' if is_on else 'OFF'}"
            self._put(scr, y, x0, w, 21, pwr_str, c_green if is_on else c_red)
            if zobj.mute:
                self._put(scr, y, x0, w, 30, "[MUTE]", c_red)
            if sel_mark:
                self._put(scr, y, x0, w, inner - len(sel_mark) - 2, sel_mark, c_cyan)
            y += 1

            self._frame_row(scr, y, x0, w, c_gold)
            self._put(scr, y, x0, w, 3, "Input :", c_dim)
            self._put(scr, y, x0, w, 11, zobj.input_name[:18], c_cyan if is_on else c_dim)
            right_info = f"Mode: {st.surround[:15]}" if zkey == "main" else "Role: Secondary Spk"
            self._put(scr, y, x0, w, inner - len(right_info) - 2, right_info, c_dim)
            y += 1

            self._frame_row(scr, y, x0, w, c_gold)
            vol_txt = fmt_vol(zobj.volume_db)
            bar_w = max(12, inner - 24)
            bar = vol_bar(zobj.volume_db, width=bar_w, muted=zobj.mute or not is_on)
            self._put(scr, y, x0, w, 3, "Vol   :", c_dim)
            self._put(scr, y, x0, w, 11, f"[{bar}]", c_cyan if (is_on and not zobj.mute) else c_dim)
            self._put(scr, y, x0, w, inner - len(vol_txt) - 2, vol_txt, c_white if is_on else c_dim)
            y += 1

            if zidx < len(zones) - 1:
                self._hline(scr, y, x0, w, "╠", "=", "╣", c_dim)
                y += 1

        self._hline(scr, y, x0, w, "╠", "=", "╣", c_gold)
        y += 1

        # Animated Stereo VU Meter
        z_focus = st.main if self.active_zone == "main" else st.z2
        vu_attr = c_cyan if (z_focus.power == "ON" and not z_focus.mute) else c_dim
        bars_l = "".join(
            VU_CHARS[min(len(VU_CHARS) - 1, int(lvl * (len(VU_CHARS) - 1)))]
            for lvl in self.vu_levels
        )
        bars_r = bars_l[::-1]

        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 2, f"VU METER  [{z_focus.name[:14]}]", c_gold)
        state_lbl = "SIGNAL LIVE" if (z_focus.power == "ON" and not z_focus.mute) else "IDLE"
        self._put(scr, y, x0, w, inner - len(state_lbl) - 2, state_lbl, c_green if state_lbl == "SIGNAL LIVE" else c_dim)
        y += 1

        self._frame_row(scr, y, x0, w, c_gold)
        vu_line = f"L [{bars_l}]   R [{bars_r}]"
        pad_vu = max(0, (inner - len(vu_line)) // 2)
        self._put(scr, y, x0, w, pad_vu, vu_line, vu_attr)
        y += 1

        self._hline(scr, y, x0, w, "╠", "=", "╣", c_gold)
        y += 1

        # Live Audio / Signal & DSP panel
        au = st.audio
        sr = au.sample_rate or "--"
        hdr_right = f"{sr} | SIG: {au.signal_mode}"
        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 2, "AUDIO / SIGNAL", c_gold)
        self._put(scr, y, x0, w, inner - len(hdr_right) - 2, hdr_right, c_cyan)
        y += 1

        if au.track and au.artist:
            now_txt = f"{au.track} - {au.artist}"
        elif au.track:
            now_txt = au.track
        elif au.stream_source:
            now_txt = f"{au.stream_source} (live stream)"
        else:
            now_txt = z_focus.input_name
        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 3, "Now  :", c_dim)
        time_col = f" {au.elapsed}" if au.elapsed else ""
        max_now = max(10, inner - 13 - len(time_col))
        self._put(scr, y, x0, w, 10, now_txt[:max_now], c_white if st.online else c_dim)
        if time_col:
            self._put(scr, y, x0, w, inner - len(time_col) - 2, time_col, c_cyan)
        y += 1

        dsp_line = f"Bass {au.bass_db:<4}  Treb {au.treble_db:<4}  Tone:{au.tone_ctrl:<3}  LFE:{au.lfe_db}"
        eq_line = f"MultEQ:{au.multeq:<7}  DynEQ:{au.dyneq:<3}  DRC:{au.drc}"
        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 3, "DSP  :", c_dim)
        self._put(scr, y, x0, w, 10, dsp_line, c_dim)
        y += 1

        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 3, "EQ   :", c_dim)
        self._put(scr, y, x0, w, 10, eq_line, c_dim)
        y += 1

        self._hline(scr, y, x0, w, "╠", "=", "╣", c_gold)
        y += 1

        # Aligned 2-column Help / Controls section
        col2 = 31
        if st.has_z2:
            self._frame_row(scr, y, x0, w, c_gold)
            self._put(scr, y, x0, w, 2, f"AIRPLAY PRESETS ({int(self.preset_z2_vol)} dB)", c_gold)
            y += 1

            z2_lbl = f"{st.z2.name[:10]} only"
            m_lbl = f"{st.main.name[:10]} only"
            for (k1, d1), (k2, d2) in (
                (("o", z2_lbl), ("l", m_lbl)),
                (("b", "Both zones"), ("", "")),
            ):
                self._frame_row(scr, y, x0, w, c_gold)
                if k1:
                    self._put(scr, y, x0, w, 3, f"{k1:<10}", c_cyan)
                    self._put(scr, y, x0, w, 14, d1, c_dim)
                if k2:
                    self._put(scr, y, x0, w, col2, f"{k2:<10}", c_cyan)
                    self._put(scr, y, x0, w, col2 + 11, d2, c_dim)
                y += 1

            self._frame_row(scr, y, x0, w, c_gold)
            y += 1

        self._frame_row(scr, y, x0, w, c_gold)
        self._put(scr, y, x0, w, 2, "CONTROLS", c_gold)
        y += 1

        for (k1, d1), (k2, d2) in (
            (("1/2 / Tab", "Switch zone"), ("+ / -", "Vol +/- 1 dB")),
            (("p", "Zone power"), ("[ / ]", "Vol +/- 3 dB")),
            (("P", "All standby"), ("m", "Mute / Unmute")),
            (("i / <- ->", "Cycle input"), ("s", "Surround mode")),
            (("e", "ECO mode"), ("r / q", "Refresh / Quit")),
        ):
            self._frame_row(scr, y, x0, w, c_gold)
            self._put(scr, y, x0, w, 3, f"{k1:<10}", c_cyan)
            self._put(scr, y, x0, w, 14, d1, c_dim)
            self._put(scr, y, x0, w, col2, f"{k2:<10}", c_cyan)
            self._put(scr, y, x0, w, col2 + 11, d2, c_dim)
            y += 1

        # Status / Toast footer
        self._hline(scr, y, x0, w, "╠", "=", "╣", c_gold)
        y += 1
        self._frame_row(scr, y, x0, w, c_gold)
        if toast:
            spinner = "|/-\\"[self.tick % 4]
            msg = f"{spinner} {toast}" if toast.startswith(">>") else toast
            self._put(scr, y, x0, w, 2, msg, c_green if msg.startswith("OK:") else c_cyan)
        elif not st.online and st.error:
            self._put(scr, y, x0, w, 2, f"Offline: {st.error}", c_red)
        else:
            age = int(time.time() - st.last_updated) if st.last_updated else 0
            self._put(scr, y, x0, w, 2, f"Ready  |  Sleep: {st.sleep}  |  Synced {age}s ago", c_dim)
        y += 1

        self._hline(scr, y, x0, w, "╚", "=", "╝", c_gold)
        scr.refresh()

    def _cycle_input(self, direction: int = 1) -> None:
        with self.lock:
            st = self.state
            zkey = self.active_zone
            zobj = st.main if zkey == "main" else st.z2
            inputs = st.inputs or [
                ("Online Music", "NET"),
                ("TV Audio", "TV"),
                ("CBL/SAT", "SAT/CBL"),
                ("DVD", "DVD"),
                ("AUX1", "AUX1"),
                ("AUX2", "AUX2"),
                ("CD", "CD"),
                ("Tuner", "TUNER"),
                ("Bluetooth", "BT"),
            ]
        cur_upper = zobj.input_name.upper()
        cur_idx = -1
        for i, (label, code) in enumerate(inputs):
            if label.upper() == cur_upper or code.upper() == cur_upper or (cur_upper == "AIRPLAY" and code == "NET"):
                cur_idx = i
                break
        next_label, next_code = inputs[(cur_idx + direction) % len(inputs)]
        self._run_async(f"{zobj.name} -> {next_label}", lambda: self.client.set_input(zkey, next_code))

    def _cycle_surround(self) -> None:
        with self.lock:
            cur = self.state.surround.upper()
        cur_idx = -1
        for i, (label, _) in enumerate(SURROUND_MODES):
            if label.upper() == cur:
                cur_idx = i
                break
        next_label, next_cmd = SURROUND_MODES[(cur_idx + 1) % len(SURROUND_MODES)]
        self._run_async(f"Mode -> {next_label}", lambda: self.client.send_cmd(next_cmd))

    def _cycle_eco(self) -> None:
        with self.lock:
            cur = self.state.eco.upper()
        idx = ECO_MODES.index(cur) if cur in ECO_MODES else -1
        nxt = ECO_MODES[(idx + 1) % len(ECO_MODES)]
        self._run_async(f"ECO -> {nxt}", lambda: self.client.send_cmd(f"ECO{nxt}"))

    def run(self, scr) -> None:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_YELLOW, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_WHITE, -1)
        curses.init_pair(5, curses.COLOR_RED, -1)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)

        scr.nodelay(True)
        scr.timeout(100)

        self.state = self.client.fetch_state()
        if self.state.online:
            au = self.client.fetch_audio_telnet(self.state.audio)
            if self.state.audio.stream_source:
                au.stream_source = self.state.audio.stream_source
            self.state.audio = au
            # Focus whichever zone is currently ON (preferring Zone 2 if Main is OFF)
            if self.state.has_z2 and self.state.z2.power == "ON" and self.state.main.power != "ON":
                self.active_zone = "z2"
            self.toast = f"Connected to {self.state.model}"
            self.toast_until = time.time() + 1.8

        poller_thread = threading.Thread(target=self._poller, daemon=True)
        poller_thread.start()

        try:
            while True:
                self._draw(scr)
                ch = scr.getch()
                self.tick += 1
                if ch == -1:
                    continue
                if ch in (ord("q"), ord("Q"), 27):
                    break
                elif ch == ord("1"):
                    self.active_zone = "main"
                elif ch == ord("2") and self.state.has_z2:
                    self.active_zone = "z2"
                elif ch == 9 and self.state.has_z2:
                    self.active_zone = "z2" if self.active_zone == "main" else "main"
                elif ch == ord("p"):
                    zkey = self.active_zone
                    with self.lock:
                        zobj = self.state.main if zkey == "main" else self.state.z2
                        target_on = zobj.power != "ON"
                        zname = zobj.name
                    self._run_async(
                        f"{zname} {'ON' if target_on else 'OFF'}",
                        lambda zk=zkey, on=target_on: self.client.set_power(zk, on),
                    )
                elif ch == ord("P"):
                    self._run_async("All Zones STANDBY", lambda: self.client.set_power("all", False))
                elif ch in (ord("+"), ord("="), curses.KEY_UP):
                    zkey = self.active_zone
                    with self.lock:
                        zobj = self.state.main if zkey == "main" else self.state.z2
                        cur_v = zobj.volume_db
                        zname = zobj.name
                    self._run_async(
                        f"{zname} Vol +1 dB",
                        lambda zk=zkey, cv=cur_v: self.client.step_volume(zk, 1.0, cv),
                    )
                elif ch in (ord("-"), ord("_"), curses.KEY_DOWN):
                    zkey = self.active_zone
                    with self.lock:
                        zobj = self.state.main if zkey == "main" else self.state.z2
                        cur_v = zobj.volume_db
                        zname = zobj.name
                    self._run_async(
                        f"{zname} Vol -1 dB",
                        lambda zk=zkey, cv=cur_v: self.client.step_volume(zk, -1.0, cv),
                    )
                elif ch == ord("]"):
                    zkey = self.active_zone
                    with self.lock:
                        zobj = self.state.main if zkey == "main" else self.state.z2
                        cur_v = zobj.volume_db
                        zname = zobj.name
                    self._run_async(
                        f"{zname} Vol +3 dB",
                        lambda zk=zkey, cv=cur_v: self.client.step_volume(zk, 3.0, cv),
                    )
                elif ch == ord("["):
                    zkey = self.active_zone
                    with self.lock:
                        zobj = self.state.main if zkey == "main" else self.state.z2
                        cur_v = zobj.volume_db
                        zname = zobj.name
                    self._run_async(
                        f"{zname} Vol -3 dB",
                        lambda zk=zkey, cv=cur_v: self.client.step_volume(zk, -3.0, cv),
                    )
                elif ch == ord("m"):
                    zkey = self.active_zone
                    with self.lock:
                        zobj = self.state.main if zkey == "main" else self.state.z2
                        nxt = not zobj.mute
                        zname = zobj.name
                    self._run_async(
                        f"{zname} {'MUTE' if nxt else 'UNMUTE'}",
                        lambda zk=zkey, m=nxt: self.client.set_mute(zk, m),
                    )
                elif ch in (ord("i"), curses.KEY_RIGHT):
                    self._cycle_input(1)
                elif ch == curses.KEY_LEFT:
                    self._cycle_input(-1)
                elif ch == ord("s"):
                    self._cycle_surround()
                elif ch == ord("e"):
                    self._cycle_eco()
                elif ch == ord("o") and self.state.has_z2:
                    self.active_zone = "z2"
                    zname = self.state.z2.name
                    self._run_async(
                        f"{zname} AirPlay ({self.preset_z2_vol:.0f} dB)",
                        lambda: self.client.preset_airplay("z2", self.preset_main_vol, self.preset_z2_vol),
                    )
                elif ch == ord("l"):
                    self.active_zone = "main"
                    mname = self.state.main.name
                    self._run_async(
                        f"{mname} AirPlay ({self.preset_main_vol:.0f} dB)",
                        lambda: self.client.preset_airplay("main", self.preset_main_vol, self.preset_z2_vol),
                    )
                elif ch == ord("b") and self.state.has_z2:
                    self._run_async(
                        f"Both Zones AirPlay ({self.preset_main_vol:.0f} dB)",
                        lambda: self.client.preset_airplay("both", self.preset_main_vol, self.preset_z2_vol),
                    )
                elif ch == ord("r"):
                    self.set_toast(">> Refreshing...")
                    self.poll_trigger.set()
        finally:
            self.stop_event.set()
            self.poll_trigger.set()
