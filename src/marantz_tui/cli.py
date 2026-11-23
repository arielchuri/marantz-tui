"""Command-line entrypoint for marantz-tui."""

from __future__ import annotations

import argparse
import curses
import sys

from . import __version__
from .client import (
    MarantzClient,
    MarantzState,
    config_path,
    discover_receivers,
    load_config,
    resolve_host,
    save_config,
)
from .tui import MarantzTUI, fmt_vol


def print_status(st: MarantzState) -> None:
    if not st.online:
        print(f"Marantz ({st.host}): OFFLINE ({st.error})")
        sys.exit(1)
    print(f"{st.model} ({st.host}) | ECO: {st.eco} | Sleep: {st.sleep}")
    print("-" * 60)
    m_mute = " [MUTE]" if st.main.mute else ""
    print(
        f"Main ({st.main.name:<9}): {st.main.power:<3} | {st.main.input_name:<12} | "
        f"{fmt_vol(st.main.volume_db)}{m_mute} | {st.surround}"
    )
    if st.has_z2:
        z_mute = " [MUTE]" if st.z2.mute else ""
        print(
            f"Z2   ({st.z2.name:<9}): {st.z2.power:<3} | {st.z2.input_name:<12} | "
            f"{fmt_vol(st.z2.volume_db)}{z_mute}"
        )
    au = st.audio
    now = f"{au.track} - {au.artist}" if (au.track and au.artist) else (au.track or au.stream_source or "--")
    sr = f" ({au.sample_rate})" if au.sample_rate else ""
    el = f" [{au.elapsed}]" if au.elapsed else ""
    print(f"Audio: {now}{sr}{el} | SIG: {au.signal_mode}")
    print(
        f"DSP  : Bass {au.bass_db} | Treb {au.treble_db} | Tone {au.tone_ctrl} | "
        f"MultEQ {au.multeq} | DynEQ {au.dyneq} | DRC {au.drc} | LFE {au.lfe_db}"
    )


def _handle_vol_arg(client: MarantzClient, zone: str, val: str) -> None:
    v = val.strip().lower()
    if v == "mute":
        client.set_mute(zone, True)
    elif v == "unmute":
        client.set_mute(zone, False)
    elif v.startswith("+") or v.startswith("-"):
        num = float(v)
        if v.startswith("+") or (v.startswith("-") and abs(num) <= 15.0):
            st = client.fetch_state()
            cur = st.main.volume_db if zone == "main" else st.z2.volume_db
            client.step_volume(zone, num, cur)
        else:
            client.set_volume_db(zone, num)
    else:
        num = float(v)
        client.set_volume_db(zone, -abs(num) if num > 0 else num)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marantz",
        description="Vintage faceplate TUI and CLI for Marantz & Denon network AV receivers",
    )
    parser.add_argument("--version", action="version", version=f"marantz-tui {__version__}")
    parser.add_argument("--host", help="Receiver IP/hostname (overrides MARANTZ_HOST and config)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("tui", help="Launch graphical TUI (default when no subcommand is given)")
    sub.add_parser("status", help="Show live Main, Zone 2, Audio & DSP status")

    p_disc = sub.add_parser("discover", help="Auto-discover Marantz/Denon receivers on LAN via SSDP")
    p_disc.add_argument("--save", action="store_true", help="Save first discovered receiver IP to config")
    p_disc.add_argument("--timeout", type=float, default=2.5, help="Discovery timeout in seconds")

    p_cfg = sub.add_parser("config", help="Show or update ~/.config/marantz-tui/config.json")
    p_cfg.add_argument("--host", dest="cfg_host", help="Save receiver IP/hostname")
    p_cfg.add_argument("--main-vol", type=float, help="Default AirPlay preset dB for Main Zone")
    p_cfg.add_argument("--z2-vol", type=float, help="Default AirPlay preset dB for Zone 2")

    sub.add_parser("on", help="Power ON whole receiver")
    sub.add_parser("off", help="Power OFF whole receiver (Standby)")

    p_main = sub.add_parser("main", help="Control Main Zone power")
    p_main.add_argument("action", choices=["on", "off"])

    p_z2 = sub.add_parser("z2", help="Control Zone 2 (on|off|vol|input)")
    p_z2.add_argument("action", help="on|off|vol|input")
    p_z2.add_argument("value", nargs="?", help="Value for vol or input")

    p_vol = sub.add_parser("vol", help="Set Main Zone volume (-40, +3, -3, mute, unmute)")
    p_vol.add_argument("value")

    p_in = sub.add_parser("input", help="Set Main Zone input (airplay, tv, cd, bt, custom name, ...)")
    p_in.add_argument("name")

    p_mode = sub.add_parser("mode", help="Set surround mode (direct, pure, stereo, auto, movie, music)")
    p_mode.add_argument("name")

    p_eco = sub.add_parser("eco", help="Set ECO mode (on, off, auto)")
    p_eco.add_argument("mode", choices=["on", "off", "auto"])

    p_sleep = sub.add_parser("sleep", help="Set sleep timer (minutes 1-120 or off)")
    p_sleep.add_argument("minutes")

    for pname in ("z2-airplay", "office", "airplay-z2", "main-airplay", "living", "airplay-main", "both-airplay", "both", "airplay-both"):
        pp = sub.add_parser(pname, help=f"AirPlay routing preset: {pname}")
        pp.add_argument("vol", nargs="?", type=float, default=None, help="Target volume in dB")

    p_raw = sub.add_parser("raw", help="Send raw Denon/Marantz HTTP command (e.g. MSDIRECT, Z2ON)")
    p_raw.add_argument("raw_cmd", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "discover":
        found = discover_receivers(timeout=args.timeout)
        if not found:
            print("No Marantz/Denon receivers discovered via SSDP.")
            print("Set manually with: marantz config --host <receiver-ip>")
            sys.exit(1)
        for idx, rec in enumerate(found, 1):
            print(f"{idx}. {rec.model} — {rec.host}")
        if args.save:
            path = save_config({"host": found[0].host})
            print(f"Saved {found[0].host} to {path}")
        return

    if args.cmd == "config":
        updates = {}
        if args.cfg_host:
            updates["host"] = args.cfg_host.strip()
        if args.main_vol is not None:
            updates["preset_main_vol"] = args.main_vol
        if args.z2_vol is not None:
            updates["preset_z2_vol"] = args.z2_vol
        if updates:
            p = save_config(updates)
            print(f"Updated {p}")
        cfg = load_config()
        print(f"Config ({config_path()}): {cfg or '(empty)'}")
        return

    host, cfg = resolve_host(args.host)
    if not host:
        print("No receiver host configured and SSDP auto-discovery found none.")
        print("Run: marantz discover --save   OR   marantz config --host <receiver-ip>")
        sys.exit(1)

    preset_main_vol = float(cfg.get("preset_main_vol", -40.0))
    preset_z2_vol = float(cfg.get("preset_z2_vol", -40.0))
    client = MarantzClient(host)

    if args.cmd in (None, "tui"):
        tui = MarantzTUI(client, preset_main_vol=preset_main_vol, preset_z2_vol=preset_z2_vol)
        curses.wrapper(tui.run)
        return

    if args.cmd == "status":
        st = client.fetch_state()
        if st.online:
            au = client.fetch_audio_telnet(st.audio)
            if st.audio.stream_source:
                au.stream_source = st.audio.stream_source
            st.audio = au
        print_status(st)
    elif args.cmd == "on":
        client.set_power("all", True)
        print("Receiver: ON")
    elif args.cmd == "off":
        client.set_power("all", False)
        print("Receiver: STANDBY")
    elif args.cmd == "main":
        client.set_power("main", args.action == "on")
        print(f"Main Zone: {args.action.upper()}")
    elif args.cmd == "z2":
        act = args.action.lower()
        if act in ("on", "off"):
            client.set_power("z2", act == "on")
            print(f"Zone 2: {act.upper()}")
        elif act == "vol" and args.value:
            _handle_vol_arg(client, "z2", args.value)
            print(f"Zone 2 vol: {args.value}")
        elif act == "input" and args.value:
            st = client.fetch_state()
            client.set_input("z2", args.value, st.inputs)
            print(f"Zone 2 input: {args.value}")
        else:
            parser.error("Usage: marantz z2 [on|off|vol <val>|input <name>]")
    elif args.cmd == "vol":
        _handle_vol_arg(client, "main", args.value)
        print(f"Main vol: {args.value}")
    elif args.cmd == "input":
        st = client.fetch_state()
        client.set_input("main", args.name, st.inputs)
        print(f"Main input: {args.name}")
    elif args.cmd == "mode":
        key = args.name.strip().lower()
        mapping = {
            "direct": "MSDIRECT",
            "pure": "MSPURE DIRECT",
            "stereo": "MSSTEREO",
            "auto": "MSAUTO",
            "movie": "MSMOVIE",
            "music": "MSMUSIC",
        }
        if key not in mapping:
            parser.error(f"Unknown mode '{args.name}'. Choose from: {', '.join(mapping)}")
        client.send_cmd(mapping[key])
        print(f"Surround mode: {key}")
    elif args.cmd == "eco":
        client.send_cmd(f"ECO{args.mode.upper()}")
        print(f"ECO: {args.mode.upper()}")
    elif args.cmd == "sleep":
        if args.minutes.lower() == "off":
            client.send_cmd("SLPOFF")
            print("Sleep: OFF")
        else:
            mins = max(1, min(120, int(args.minutes)))
            client.send_cmd(f"SLP{mins:03d}")
            print(f"Sleep: {mins} min")
    elif args.cmd in ("z2-airplay", "office", "airplay-z2"):
        v = args.vol if args.vol is not None else preset_z2_vol
        client.preset_airplay("z2", main_vol=preset_main_vol, z2_vol=v)
        print(f"Zone 2 AirPlay active ({v:.1f} dB, Main OFF)")
    elif args.cmd in ("main-airplay", "living", "airplay-main"):
        v = args.vol if args.vol is not None else preset_main_vol
        client.preset_airplay("main", main_vol=v, z2_vol=preset_z2_vol)
        print(f"Main Zone AirPlay active ({v:.1f} dB, Zone 2 OFF)")
    elif args.cmd in ("both-airplay", "both", "airplay-both"):
        v = args.vol if args.vol is not None else preset_main_vol
        client.preset_airplay("both", main_vol=v, z2_vol=v)
        print(f"Both Zones AirPlay active ({v:.1f} dB)")
    elif args.cmd == "raw":
        raw_cmd = " ".join(args.raw_cmd)
        client.send_cmd(raw_cmd)
        print(f"Sent: {raw_cmd}")


if __name__ == "__main__":
    main()
