"""HTTP/XML, Telnet (port 23), SSDP discovery, and config management for Marantz/Denon AVRs."""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TIMEOUT = 2.5

# Standard Denon/Marantz SI / Z2 protocol input codes
INPUT_CODE_MAP = {
    "NETWORK": "NET",
    "ONLINE MUSIC": "NET",
    "AIRPLAY": "NET",
    "NET": "NET",
    "CBL/SAT": "SAT/CBL",
    "SAT/CBL": "SAT/CBL",
    "DVD": "DVD",
    "TV AUDIO": "TV",
    "TV": "TV",
    "AUX1": "AUX1",
    "AUX2": "AUX2",
    "CD": "CD",
    "TUNER": "TUNER",
    "IPOD/USB": "USB/IPOD",
    "USB/IPOD": "USB/IPOD",
    "USB": "USB/IPOD",
    "BLUETOOTH": "BT",
    "BT": "BT",
    "BLU-RAY": "BD",
    "BD": "BD",
    "GAME": "GAME",
    "MEDIA PLAYER": "MPLAY",
    "MPLAY": "MPLAY",
    "PHONO": "PHONO",
}

SURROUND_MODES = [
    ("Direct", "MSDIRECT"),
    ("Pure Direct", "MSPURE DIRECT"),
    ("Stereo", "MSSTEREO"),
    ("Auto", "MSAUTO"),
    ("Movie", "MSMOVIE"),
    ("Music", "MSMUSIC"),
]

ECO_MODES = ["ON", "AUTO", "OFF"]


@dataclass
class ZoneState:
    name: str = ""
    power: str = "OFF"
    input_name: str = "--"
    volume_db: Optional[float] = None
    mute: bool = False


@dataclass
class AudioInfo:
    stream_source: str = ""
    track: str = ""
    artist: str = ""
    album: str = ""
    sample_rate: str = ""
    elapsed: str = ""
    signal_mode: str = "--"
    tone_ctrl: str = "--"
    bass_db: str = "--"
    treble_db: str = "--"
    multeq: str = "--"
    dyneq: str = "--"
    drc: str = "--"
    lfe_db: str = "--"


@dataclass
class MarantzState:
    host: str = ""
    model: str = "Marantz AVR"
    online: bool = False
    error: str = ""
    has_z2: bool = True
    main: ZoneState = field(default_factory=lambda: ZoneState(name="Main Zone"))
    z2: ZoneState = field(default_factory=lambda: ZoneState(name="Zone 2"))
    audio: AudioInfo = field(default_factory=AudioInfo)
    surround: str = "--"
    eco: str = "--"
    sleep: str = "Off"
    inputs: list[tuple[str, str]] = field(default_factory=list)  # (display_name, cmd_code)
    last_updated: float = 0.0


@dataclass
class DiscoveredReceiver:
    host: str
    model: str = "Marantz/Denon AVR"


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "marantz-tui" / "config.json"


def load_config(path: Optional[Path] = None) -> dict:
    p = path or config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(updates: dict, path: Optional[Path] = None) -> Path:
    p = path or config_path()
    current = load_config(p)
    current.update({k: v for k, v in updates.items() if v is not None})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return p


def discover_receivers(timeout: float = 2.0) -> list[DiscoveredReceiver]:
    """Discover Marantz/Denon AV receivers on the local LAN via SSDP M-SEARCH."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        "\r\n"
    ).encode("ascii")

    candidates: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.sendto(msg, ("239.255.255.250", 1900))
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            sock.settimeout(remaining)
            try:
                data, (ip, _) = sock.recvfrom(4096)
            except socket.timeout:
                break
            text = data.decode("utf-8", errors="ignore")
            lower = text.lower()
            if any(k in lower for k in ("marantz", "denon", "d&m", "knos", "upnp")):
                candidates.add(ip)
    except OSError:
        pass
    finally:
        sock.close()

    found: list[DiscoveredReceiver] = []
    for ip in sorted(candidates):
        try:
            url = f"http://{ip}/goform/formMainZone_MainZoneXml.xml"
            req = urllib.request.Request(url, headers={"User-Agent": "marantz-tui/0.1"})
            with urllib.request.urlopen(req, timeout=1.2) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
            root = ET.fromstring(xml_text)
            model = _xml_text(root, "FriendlyName", "")
            brand = _xml_text(root, "BrandId", "")
            if model or brand:
                found.append(DiscoveredReceiver(host=ip, model=model or brand))
        except Exception:
            continue
    return found


def resolve_host(cli_host: Optional[str] = None) -> tuple[Optional[str], dict]:
    """Resolve AVR host: --host > MARANTZ_HOST > config.json > SSDP discovery."""
    cfg = load_config()
    if cli_host:
        return cli_host.strip(), cfg
    env_host = os.environ.get("MARANTZ_HOST", "").strip()
    if env_host:
        return env_host, cfg
    cfg_host = str(cfg.get("host", "")).strip()
    if cfg_host:
        return cfg_host, cfg
    discovered = discover_receivers(timeout=1.8)
    if discovered:
        return discovered[0].host, cfg
    return None, cfg


def _xml_text(root: ET.Element, tag: str, default: str = "") -> str:
    el = root.find(f".//{tag}/value")
    if el is not None and el.text is not None:
        return el.text.strip()
    el2 = root.find(f".//{tag}")
    if el2 is not None and el2.text is not None:
        return el2.text.strip()
    return default


def _parse_vol(raw: str) -> Optional[float]:
    raw = raw.strip()
    if not raw or raw == "--":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_main_xml(xml_text: str, st: MarantzState) -> None:
    mroot = ET.fromstring(xml_text)
    st.model = _xml_text(mroot, "FriendlyName", st.model)
    st.main.name = _xml_text(mroot, "RenameZone", "Main Zone") or "Main Zone"
    st.main.power = _xml_text(mroot, "ZonePower", _xml_text(mroot, "Power", "OFF")).upper()
    st.main.input_name = _xml_text(mroot, "InputFuncSelect", "--")
    st.main.volume_db = _parse_vol(_xml_text(mroot, "MasterVolume", "--"))
    st.main.mute = _xml_text(mroot, "Mute", "off").lower() == "on"
    st.surround = _xml_text(mroot, "selectSurround", "--").strip() or "--"
    st.eco = _xml_text(mroot, "ECOMode", "").strip() or "--"
    st.sleep = _xml_text(mroot, "SleepOff", "Off").strip() or "Off"


def parse_zone2_xml(xml_text: str, st: MarantzState) -> None:
    zroot = ET.fromstring(xml_text)
    st.z2.name = _xml_text(zroot, "Zone", "Zone 2") or "Zone 2"
    st.z2.power = _xml_text(zroot, "Power", "OFF").upper()
    st.z2.input_name = _xml_text(zroot, "InputFuncSelect", "--")
    st.z2.volume_db = _parse_vol(_xml_text(zroot, "MasterVolume", "--"))
    st.z2.mute = _xml_text(zroot, "Mute", "off").lower() == "on"

    func_vals = [e.text.strip() for e in zroot.findall("./InputFuncList/value") if e.text]
    rename_vals = []
    for e in zroot.findall("./RenameSource/value"):
        inner = e.find("value")
        txt = (inner.text if inner is not None else e.text) or ""
        rename_vals.append(txt.strip())
    del_vals = [(e.text or "").strip() for e in zroot.findall("./SourceDelete/value")]

    inputs: list[tuple[str, str]] = []
    for idx, func in enumerate(func_vals):
        if idx < len(del_vals) and del_vals[idx].upper() == "DEL":
            continue
        label = rename_vals[idx] if idx < len(rename_vals) and rename_vals[idx] else func
        code = INPUT_CODE_MAP.get(func.upper(), func.upper())
        inputs.append((label, code))
    if inputs:
        st.inputs = inputs
    st.has_z2 = True


def parse_telnet_response(raw_text: str, prev: Optional[AudioInfo] = None) -> AudioInfo:
    info = AudioInfo(
        stream_source=prev.stream_source if prev else "",
        track=prev.track if prev else "",
        artist=prev.artist if prev else "",
        album=prev.album if prev else "",
        sample_rate=prev.sample_rate if prev else "",
        elapsed=prev.elapsed if prev else "",
        signal_mode=prev.signal_mode if prev else "--",
        tone_ctrl=prev.tone_ctrl if prev else "--",
        bass_db=prev.bass_db if prev else "--",
        treble_db=prev.treble_db if prev else "--",
        multeq=prev.multeq if prev else "--",
        dyneq=prev.dyneq if prev else "--",
        drc=prev.drc if prev else "--",
        lfe_db=prev.lfe_db if prev else "--",
    )

    nse_lines: dict[int, str] = {}
    for line in raw_text.replace("\n", "\r").split("\r"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("SD"):
            info.signal_mode = line[2:].strip() or "--"
        elif line.startswith("PSTONE CTRL "):
            info.tone_ctrl = line[12:].strip() or "--"
        elif line.startswith("PSBAS "):
            val = line[6:].strip()
            if val.isdigit():
                info.bass_db = f"{int(val) - 50:+d}dB"
        elif line.startswith("PSTRE "):
            val = line[6:].strip()
            if val.isdigit():
                info.treble_db = f"{int(val) - 50:+d}dB"
        elif line.startswith("PSMULTEQ:"):
            info.multeq = line[9:].strip() or "--"
        elif line.startswith("PSDYNEQ "):
            info.dyneq = line[8:].strip() or "--"
        elif line.startswith("PSDRC "):
            info.drc = line[6:].strip() or "--"
        elif line.startswith("PSLFE "):
            val = line[6:].strip()
            if val.isdigit():
                info.lfe_db = f"{-int(val)}dB"
        elif line.startswith("NSE") and len(line) >= 4 and line[3].isdigit():
            idx = int(line[3])
            txt = re.sub(r"[\x00-\x1f]", "", line[4:]).strip()
            nse_lines[idx] = txt

    if nse_lines:
        info.track = nse_lines.get(1, "")
        info.artist = nse_lines.get(2, "")
        l3 = nse_lines.get(3, "")
        if "khz" in l3.lower() or "hz" in l3.lower():
            info.sample_rate = l3
        else:
            info.album = l3
        l4 = nse_lines.get(4, "")
        if l4 and not info.album:
            info.album = l4
        l5 = nse_lines.get(5, "")
        m_time = re.search(r"\d+:\d{2}", l5)
        info.elapsed = m_time.group(0) if m_time else ""
    return info


def format_volume_cmd(zone: str, db: float) -> str:
    """Format a Denon/Marantz volume command, safely clamped to -80..0 dB."""
    clamped = max(-80.0, min(0.0, db))
    raw = 80.0 + clamped
    if zone == "main":
        halves = round(raw * 2) / 2.0
        whole = int(halves)
        if abs(halves - whole) >= 0.25:
            return f"MV{whole:02d}5"
        return f"MV{whole:02d}"
    return f"Z2{int(round(raw)):02d}"


class MarantzClient:
    def __init__(self, host: str) -> None:
        self.host = host
        self.base = f"http://{host}"

    def _get(self, path: str) -> str:
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "marantz-tui/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def send_cmd(self, cmd: str) -> None:
        encoded = urllib.parse.quote(cmd.strip(), safe="/")
        self._get(f"/goform/formiPhoneAppDirect.xml?{encoded}")

    def fetch_state(self) -> MarantzState:
        st = MarantzState(host=self.host)
        try:
            parse_main_xml(self._get("/goform/formMainZone_MainZoneXml.xml"), st)

            # Main lite XML gives exact "AirPlay" vs "Online Music"
            try:
                mlite = ET.fromstring(self._get("/goform/formMainZone_MainZoneXmlStatusLite.xml"))
                lite_in = _xml_text(mlite, "InputFuncSelect", "")
                if lite_in:
                    st.main.input_name = lite_in
            except Exception:
                pass

            # Zone 2 XML (optional on single-zone receivers)
            try:
                parse_zone2_xml(self._get("/goform/formZone2_Zone2XmlStatus.xml"), st)
            except Exception:
                st.has_z2 = False

            # NetAudio XML gives active streaming source label
            try:
                net_xml = self._get("/goform/formNetAudio_StatusXml.xml")
                nroot = ET.fromstring(net_xml)
                st.audio.stream_source = _xml_text(nroot, "NetPlayingTitle", "")
            except Exception:
                pass

            st.online = True
            st.last_updated = time.time()
        except Exception as exc:
            st.online = False
            st.error = str(exc)
        return st

    def fetch_audio_telnet(self, prev: Optional[AudioInfo] = None) -> AudioInfo:
        """Query live DSP/EQ & Now Playing metadata over Telnet port 23 and close immediately."""
        try:
            sock = socket.create_connection((self.host, 23), timeout=1.2)
            sock.settimeout(0.25)
            cmds = [
                "SD?",
                "PSBAS ?",
                "PSTRE ?",
                "PSTONE CTRL ?",
                "PSMULTEQ: ?",
                "PSDYNEQ ?",
                "PSDRC ?",
                "PSLFE ?",
                "NSE",
            ]
            for c in cmds:
                sock.sendall((c + "\r").encode("ascii"))
                time.sleep(0.03)
            buf = bytearray()
            deadline = time.time() + 0.45
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)
                except socket.timeout:
                    break
            sock.close()
            return parse_telnet_response(buf.decode("utf-8", errors="replace"), prev)
        except Exception:
            return prev if prev is not None else AudioInfo()

    def set_volume_db(self, zone: str, db: float) -> None:
        self.send_cmd(format_volume_cmd(zone, db))

    def step_volume(self, zone: str, delta_db: float, current_db: Optional[float]) -> None:
        if current_db is not None:
            self.set_volume_db(zone, current_db + delta_db)
        elif zone == "main":
            self.send_cmd("MVUP" if delta_db > 0 else "MVDOWN")
        else:
            self.send_cmd("Z2UP" if delta_db > 0 else "Z2DOWN")

    def set_power(self, zone: str, on: bool) -> None:
        if zone == "all":
            self.send_cmd("PWON" if on else "PWSTANDBY")
        elif zone == "main":
            self.send_cmd("ZMON" if on else "ZMOFF")
        else:
            self.send_cmd("Z2ON" if on else "Z2OFF")

    def set_mute(self, zone: str, muted: bool) -> None:
        if zone == "main":
            self.send_cmd("MUON" if muted else "MUOFF")
        else:
            self.send_cmd("Z2MUON" if muted else "Z2MUOFF")

    def resolve_input_code(self, name_or_code: str, custom_inputs: Optional[list[tuple[str, str]]] = None) -> str:
        key = name_or_code.strip().upper()
        if custom_inputs:
            for label, code in custom_inputs:
                if label.upper() == key or label.upper().startswith(key):
                    return code
        return INPUT_CODE_MAP.get(key, key)

    def set_input(self, zone: str, name_or_code: str, custom_inputs: Optional[list[tuple[str, str]]] = None) -> None:
        code = self.resolve_input_code(name_or_code, custom_inputs)
        prefix = "SI" if zone == "main" else "Z2"
        self.send_cmd(f"{prefix}{code}")

    def preset_airplay(self, target: str, main_vol: float = -40.0, z2_vol: float = -40.0) -> None:
        if target == "z2":
            self.set_power("main", False)
            time.sleep(0.15)
            self.set_power("z2", True)
            time.sleep(0.15)
            self.set_input("z2", "NET")
            time.sleep(0.15)
            self.set_volume_db("z2", z2_vol)
        elif target == "main":
            self.set_power("z2", False)
            time.sleep(0.15)
            self.set_power("main", True)
            time.sleep(0.15)
            self.set_input("main", "NET")
            time.sleep(0.15)
            self.set_volume_db("main", main_vol)
        elif target == "both":
            self.set_power("main", True)
            self.set_power("z2", True)
            time.sleep(0.15)
            self.set_input("main", "NET")
            self.set_input("z2", "NET")
            time.sleep(0.15)
            self.set_volume_db("main", main_vol)
            self.set_volume_db("z2", z2_vol)
