# marantz-tui

Zero-dependency vintage faceplate TUI and CLI for controlling **Marantz** and **Denon** network AV receivers over your local LAN.

Built with Python 3 standard library only (`curses`, `urllib`, `socket`, `xml.etree.ElementTree`) — no external packages, no cloud accounts, starts in under 50 ms.

```text
╔============================================================╗
║           __  __   _   ___   _   _  _ _____ ____           ║
║          |  \/  | /_\ | _ \ /_\ | \| |_   _|_  /           ║
║          | |\/| |/ _ \|   // _ \| .` | | |  / /            ║
║          |_|  |_/_/ \_\_|_\_/ \_\_|\_| |_| /___|           ║
║  Marantz NR1606  |  ECO: OFF            (*) 192.168.1.251  ║
╠============================================================╣
║   [1] MAIN ZONE     ( ) OFF                                ║
║   Input : AirPlay                            Mode: Stereo  ║
║   Vol   : [▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒··················] -39.5 dB  ║
╠============================================================╣
║   [2] ZONE 2        (*) ON                      << ACTIVE  ║
║   Input : AirPlay                     Role: Secondary Spk  ║
║   Vol   : [████████████████████░░░░░░░░░░░░░░░░] -35.0 dB  ║
╠============================================================╣
║  VU METER  [Zone 2]                           SIGNAL LIVE  ║
║      L [▃▃▁▁   ▁▁▂▂▁▁▁▁▂▂ ]   R [ ▂▂▁▁▁▁▂▂▁▁   ▁▁▃▃]       ║
╠============================================================╣
║  AUDIO / SIGNAL                       44.1kHz | SIG: AUTO  ║
║   Now  : AirPlay (live stream)                             ║
║   DSP  : Bass +0dB  Treb +0dB  Tone:OFF  LFE:0dB           ║
║   EQ   : MultEQ:OFF      DynEQ:OFF  DRC:OFF                ║
╠============================================================╣
║  AIRPLAY PRESETS (-40 dB)                                  ║
║   o          Zone 2 only      l          Main Zone only    ║
║   b          Both zones                                    ║
║                                                            ║
║  CONTROLS                                                  ║
║   1/2 / Tab  Switch zone      + / -      Vol +/- 1 dB      ║
║   p          Zone power       [ / ]      Vol +/- 3 dB      ║
║   P          All standby      m          Mute / Unmute     ║
║   i / <- ->  Cycle input      s          Surround mode     ║
║   e          ECO mode         r / q      Refresh / Quit    ║
╠============================================================╣
║  Ready  |  Sleep: Off  |  Synced 1s ago                    ║
╚============================================================╝
```

## Features

- **Compact 62-column animated faceplate:** Sweeping gold/cyan ASCII headline, pulsing power/link LEDs, smooth block volume bars (`█░`), and animated stereo VU meter (`▁▂▃▄▅▆▇█`).
- **Dual-zone control:** Independently control Main Zone and Zone 2 (power, volume in 0.5/1 dB steps, mute, input switching). Automatically reads your receiver's custom zone names and renamed/hidden inputs.
- **One-key AirPlay Zone 2 routing:** AirPlay 1 receivers always wake Main Zone when streaming starts. Press `o` (or run `marantz z2-airplay`) to switch Main Zone OFF, turn Zone 2 ON, select AirPlay/NET, and set a safe default volume in one shot.
- **Live Audio / Signal & DSP panel:** Polls sample rate (`44.1kHz`, `48kHz`, etc.), Now Playing metadata (track/artist/elapsed time when available), signal mode (`AUTO`/`HDMI`/`DIGITAL`), Bass/Treble/Tone, Audyssey MultEQ, Dynamic EQ, DRC, and LFE via Telnet port 23 without locking out the mobile app.
- **SSDP auto-discovery:** Automatically finds Marantz and Denon receivers on your local network (`marantz discover --save`).
- **Zero dependencies:** Pure Python 3.10+ stdlib. Works on macOS and Linux.

## Install

### Using `pipx` (recommended)
```bash
pipx install git+https://github.com/arielchuri/marantz-tui.git
```

### Using `uv`
```bash
uv tool install git+https://github.com/arielchuri/marantz-tui.git
```

### Using `pip`
```bash
pip install git+https://github.com/arielchuri/marantz-tui.git
```

### From a local clone
```bash
git clone https://github.com/arielchuri/marantz-tui.git
cd marantz-tui
pip install -e .
```

## Quick Start

1. **Discover and save your receiver's IP:**
   ```bash
   marantz discover --save
   ```
   Or set it manually:
   ```bash
   marantz config --host 192.168.1.251
   ```
   *(Tip: assign a static DHCP lease to your receiver on your router so its IP never changes.)*

2. **Launch the TUI:**
   ```bash
   marantz
   ```

## CLI Reference

```bash
marantz                             # Launch graphical TUI
marantz discover [--save]           # Auto-discover Marantz/Denon receivers via SSDP
marantz config --host 192.168.1.50  # Save host and default preset volumes
marantz status                      # Print live Main, Zone 2, Audio & DSP status

# Power & Zones
marantz on | off                    # Whole receiver power / standby
marantz main on | off               # Main Zone power
marantz z2 on | off                 # Zone 2 power

# Volume & Mute (dB clamped safely to -80..0 dB)
marantz vol -40 | +3 | -3 | mute | unmute
marantz z2 vol -42 | +2 | -2 | mute | unmute

# Inputs, Surround, ECO & Sleep
marantz input airplay | tv | cd | tuner | aux1 | aux2 | bt | sat/cbl | dvd
marantz z2 input airplay | ...
marantz mode direct | pure | stereo | auto | movie | music
marantz eco on | auto | off
marantz sleep 30 | off

# One-shot AirPlay Presets (optional target dB, default from config or -40)
marantz z2-airplay [-40]            # Main OFF, Zone 2 ON -> AirPlay (alias: office)
marantz main-airplay [-40]          # Main ON -> AirPlay, Zone 2 OFF (alias: living)
marantz both-airplay [-40]          # Both zones ON -> AirPlay (alias: both)

# Raw Denon/Marantz Protocol Command
marantz raw MSDIRECT
```

## Configuration

Host resolution order:
1. `--host <ip>` CLI flag
2. `MARANTZ_HOST` environment variable
3. `~/.config/marantz-tui/config.json` (created by `marantz discover --save` or `marantz config`)
4. Automatic SSDP LAN discovery fallback

Example `~/.config/marantz-tui/config.json`:
```json
{
  "host": "192.168.1.251",
  "preset_main_vol": -40.0,
  "preset_z2_vol": -40.0
}
```

## Compatibility

Tested on **Marantz NR1606**. Compatible with most network-enabled **Marantz** (NR, SR, Cinema, M-CR series) and **Denon** (AVR-X, AVR-S series) receivers that expose the standard Denon/Marantz HTTP XML (`/goform/`) and Telnet (port 23) control protocol.

## License

MIT — see [LICENSE](LICENSE).
