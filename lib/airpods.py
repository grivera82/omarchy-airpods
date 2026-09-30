"""Control AirPods from Omarchy (backend for grivera.airpods).

Pairing and connection go through BlueZ (bluetoothctl). Everything AirPods
specific (per-bud battery, ear detection, noise control, conversational
awareness) uses Apple's AAP protocol over L2CAP PSM 0x1001, as documented by
the LibrePods project. Standard library only.

`airpods daemon` keeps one AAP session open while the AirPods are connected.
The Omarchy service talks to it over stdin/stdout (JSON lines); the CLI talks
to it over a unix socket.
"""

import argparse
import asyncio
import fcntl
import json
import os
import re
import socket
import sys
import threading
from typing import Optional

STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "grivera-airpods",
)
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
SOCKET = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or STATE_DIR, "grivera-airpods.sock")

APPLE_VENDOR = "004C"
# Modalias product id -> (name, noise modes the model supports)
BASIC = ["off"]
ANC = ["off", "transparency", "anc"]
ADAPTIVE = ["off", "transparency", "adaptive", "anc"]
MODELS = {
    "2002": ("AirPods", BASIC),
    "200F": ("AirPods (2nd gen)", BASIC),
    "2013": ("AirPods (3rd gen)", BASIC),
    "2019": ("AirPods 4", BASIC),
    "201B": ("AirPods 4 (ANC)", ADAPTIVE),
    "200E": ("AirPods Pro", ANC),
    "2014": ("AirPods Pro 2", ADAPTIVE),
    "2024": ("AirPods Pro 2 (USB-C)", ADAPTIVE),
    "2027": ("AirPods Pro 3", ADAPTIVE),
    "200A": ("AirPods Max", ANC),
    "201F": ("AirPods Max (USB-C)", ADAPTIVE),
}

# ---- AAP protocol --------------------------------------------------------

AAP_PSM = 0x1001
HEADER = b"\x04\x00\x04\x00"
HANDSHAKE = bytes.fromhex("00000400010002000000000000000000")
SET_FEATURES = bytes.fromhex("040004004d00ff00000000000000")
REQUEST_NOTIFICATIONS = bytes.fromhex("040004000f00ffffffff")

OP_BATTERY = 0x04
OP_EAR = 0x06
OP_CONTROL = 0x09
CTL_NOISE_MODE = 0x0D
CTL_CONVERSATIONAL = 0x28

NOISE_MODES = {1: "off", 2: "anc", 3: "transparency", 4: "adaptive"}
NOISE_CODES = {v: k for k, v in NOISE_MODES.items()}
BATTERY_PARTS = {0x01: "single", 0x02: "right", 0x04: "left", 0x08: "case"}
EAR_STATES = {0: "ear", 1: "out", 2: "case"}


def control_packet(identifier: int, value: int) -> bytes:
    return HEADER + bytes([OP_CONTROL, 0x00, identifier, value, 0x00, 0x00, 0x00])


def parse_packet(data: bytes) -> dict:
    """Decode an AAP notification into state changes."""
    if len(data) < 7 or not data.startswith(HEADER):
        return {}
    op = data[4] | (data[5] << 8)
    if op == OP_BATTERY:
        battery, i = {}, 7
        for _ in range(data[6]):
            if i + 3 >= len(data):
                break
            part = BATTERY_PARTS.get(data[i])
            level, status = data[i + 2], data[i + 3]
            if part and status != 0x04:  # 0x04 = not connected / unknown
                battery[part] = {"level": level, "charging": status == 0x01}
            i += 5
        return {"battery": battery}
    if op == OP_EAR and len(data) >= 8:
        return {"ear": [EAR_STATES.get(data[6], "unknown"), EAR_STATES.get(data[7], "unknown")]}
    if op == OP_CONTROL and len(data) >= 8:
        if data[6] == CTL_NOISE_MODE:
            return {"noiseMode": NOISE_MODES.get(data[7])}
        if data[6] == CTL_CONVERSATIONAL:
            return {"conversationalAwareness": data[7] == 0x01}
    return {}


class AAPSession:
    """One L2CAP connection to the AirPods, read on a background thread."""

    def __init__(self, address: str, loop, on_update, on_closed):
        self.address = address
        self.loop = loop
        self.on_update = on_update
        self.on_closed = on_closed
        self.sock: Optional[socket.socket] = None
        self.closed = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        error = None
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            sock.settimeout(8)
            sock.connect((self.address, AAP_PSM))
            sock.settimeout(None)
            self.sock = sock
            for packet in (HANDSHAKE, SET_FEATURES, REQUEST_NOTIFICATIONS):
                sock.send(packet)
            while not self.closed:
                data = sock.recv(1024)
                if not data:
                    break
                update = parse_packet(data)
                if update:
                    self.loop.call_soon_threadsafe(self.on_update, update)
        except OSError as exc:
            error = str(exc)
        finally:
            self.closed = True
            if self.sock:
                self.sock.close()
            self.loop.call_soon_threadsafe(self.on_closed, self, error)

    def send(self, packet: bytes):
        if self.sock is None or self.closed:
            raise CommandError("AirPods control channel is not connected")
        self.sock.send(packet)

    def close(self):
        self.closed = True
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


# ---- helpers -------------------------------------------------------------


class CommandError(Exception):
    pass


def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f)
    os.replace(tmp, CONFIG_FILE)


async def run(*cmd, timeout: float = 20) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise CommandError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} timed out")
    return re.sub(r"\x1b\[[0-9;]*m", "", out.decode(errors="replace"))


async def bt_info(mac: str) -> dict:
    out = await run("bluetoothctl", "info", mac, timeout=5)
    info = {"mac": mac}
    for line in out.splitlines():
        key, _, value = line.strip().partition(": ")
        if key in ("Name", "Alias", "Paired", "Trusted", "Connected", "Modalias", "Icon"):
            info[key.lower()] = value
        elif key == "Battery Percentage":
            m = re.search(r"\((\d+)\)", value)
            info["battery"] = int(m.group(1)) if m else None
    return info


async def bt_devices(kind: Optional[str] = None) -> list:
    out = await run("bluetoothctl", "devices", *([kind] if kind else []), timeout=5)
    return [(m.group(1), m.group(2)) for m in
            re.finditer(r"^Device ([0-9A-F:]{17}) (.*)$", out, re.M)]


def model_of(info: dict) -> Optional[tuple]:
    m = re.search(r"v([0-9A-F]{4})p([0-9A-F]{4})", info.get("modalias", ""), re.I)
    if m and m.group(1).upper() == APPLE_VENDOR:
        return MODELS.get(m.group(2).upper(), ("AirPods", ADAPTIVE))
    if "airpods" in (info.get("name") or "").lower():
        return ("AirPods", ADAPTIVE)
    return None


# ---- media auto-pause (MPRIS) ---------------------------------------------


async def mpris_players() -> list:
    out = await run("gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                    "--object-path", "/org/freedesktop/DBus",
                    "--method", "org.freedesktop.DBus.ListNames", timeout=3)
    return re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", out)


async def mpris_status(player: str) -> str:
    out = await run("gdbus", "call", "--session", "--dest", player,
                    "--object-path", "/org/mpris/MediaPlayer2",
                    "--method", "org.freedesktop.DBus.Properties.Get",
                    "org.mpris.MediaPlayer2.Player", "PlaybackStatus", timeout=3)
    m = re.search(r"'(\w+)'", out)
    return m.group(1) if m else ""


async def mpris_call(player: str, method: str):
    await run("gdbus", "call", "--session", "--dest", player,
              "--object-path", "/org/mpris/MediaPlayer2",
              "--method", f"org.mpris.MediaPlayer2.Player.{method}", timeout=3)


# ---- controller ------------------------------------------------------------


class Controller:
    def __init__(self, on_state=None):
        self.loop = asyncio.get_running_loop()
        self.config = load_config()
        self.config.setdefault("autoPause", True)
        self.on_state = on_state or (lambda state: None)
        self.state = {"status": "starting", "autoPause": self.config["autoPause"]}
        self.aap: Optional[AAPSession] = None
        self.paused_by_us: list = []
        self.pair_task: Optional[asyncio.Task] = None
        self._refresh_lock = asyncio.Lock()

    def publish(self, **changes):
        self.state.update(changes)
        self.on_state(dict(self.state))

    # ---- device ----

    async def find_airpods(self) -> Optional[dict]:
        """The saved AirPods, else the first paired Apple audio device."""
        saved = self.config.get("mac")
        paired = await bt_devices("Paired")
        macs = [mac for mac, _ in paired]
        if saved in macs:
            macs.remove(saved)
            macs.insert(0, saved)
        for mac in macs:
            info = await bt_info(mac)
            if model_of(info):
                if mac != saved:
                    self.config["mac"] = mac
                    save_config(self.config)
                return info
        return None

    async def refresh(self):
        async with self._refresh_lock:
            info = await self.find_airpods()
            if info is None:
                self._stop_aap()
                self.publish(status="unpaired", device=None, battery={}, ear=None,
                             noiseMode=None, conversationalAwareness=None)
                return
            name, modes = model_of(info)
            connected = info.get("connected") == "yes"
            self.publish(
                device={"mac": info["mac"], "name": info.get("alias") or info.get("name"),
                        "model": name, "modes": modes},
                status="connected" if connected else "disconnected",
            )
            if connected and self.aap is None:
                # Give A2DP a moment to settle before opening the control channel.
                await asyncio.sleep(1)
                self.aap = AAPSession(info["mac"], self.loop, self._aap_update, self._aap_closed)
            elif not connected:
                self._stop_aap()
                self.publish(battery={}, ear=None, noiseMode=None,
                             conversationalAwareness=None, control=False)
            if connected and not self.state.get("battery") and info.get("battery") is not None:
                self.publish(battery={"single": {"level": info["battery"], "charging": False}})

    def _stop_aap(self):
        if self.aap:
            self.aap.close()
            self.aap = None

    def _aap_update(self, update: dict):
        if "ear" in update:
            asyncio.create_task(self._auto_pause(self.state.get("ear"), update["ear"]))
        if "battery" in update:
            # Keep parts not in this packet (e.g. the case after it closes).
            merged = dict(self.state.get("battery") or {})
            merged.pop("single", None)
            merged.update(update["battery"])
            update = {**update, "battery": merged}
        self.publish(control=True, **update)

    def _aap_closed(self, session, error):
        if self.aap is session:
            self.aap = None
            self.publish(control=False, controlError=error)
            asyncio.create_task(self._retry_aap())

    async def _retry_aap(self):
        await asyncio.sleep(5)
        await self.refresh()

    async def _auto_pause(self, before, after):
        """Pause when an AirPod leaves your ear, resume when it's back."""
        if not self.config.get("autoPause") or not before or not after:
            return
        was_in = before.count("ear")
        now_in = after.count("ear")
        try:
            if now_in < was_in and was_in == 2 or (now_in == 0 and was_in > 0):
                self.paused_by_us = []
                for player in await mpris_players():
                    if await mpris_status(player) == "Playing":
                        await mpris_call(player, "Pause")
                        self.paused_by_us.append(player)
            elif now_in > was_in and now_in == 2 or (was_in == 0 and now_in > 0 and self.paused_by_us):
                for player in self.paused_by_us:
                    await mpris_call(player, "Play")
                self.paused_by_us = []
        except CommandError:
            pass

    async def watch_bluez(self):
        """Refresh whenever BlueZ reports a change, plus a slow poll."""
        async def poll():
            while True:
                await asyncio.sleep(20)
                await self.refresh()

        asyncio.create_task(poll())
        proc = await asyncio.create_subprocess_exec(
            "gdbus", "monitor", "--system", "--dest", "org.bluez",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        pending = None
        while line := await proc.stdout.readline():
            if b"Connected" not in line and b"InterfacesAdded" not in line \
                    and b"InterfacesRemoved" not in line and b"Paired" not in line:
                continue
            if pending:
                pending.cancel()
            pending = self.loop.call_later(0.5, lambda: asyncio.create_task(self.refresh()))

    # ---- commands ----

    async def handle(self, msg: dict):
        fn = getattr(self, f"cmd_{msg.get('cmd')}", None)
        if fn is None:
            raise CommandError(f"unknown command {msg.get('cmd')!r}")
        return await fn(**{k: v for k, v in msg.items() if k not in ("cmd", "id")})

    async def _device(self) -> dict:
        if not self.state.get("device"):
            await self.refresh()
        if not self.state.get("device"):
            raise CommandError("no AirPods paired; run `airpods pair`")
        return self.state["device"]

    async def cmd_status(self):
        await self.refresh()
        return dict(self.state)

    async def cmd_connect(self):
        dev = await self._device()
        self.publish(status="connecting")
        out = await run("bluetoothctl", "connect", dev["mac"], timeout=20)
        await self.refresh()
        if self.state.get("status") != "connected":
            raise CommandError("connect failed: " + (out.strip().splitlines() or ["?"])[-1])
        return "connected"

    async def cmd_disconnect(self):
        dev = await self._device()
        self._stop_aap()
        await run("bluetoothctl", "disconnect", dev["mac"], timeout=10)
        await self.refresh()
        return "disconnected"

    async def cmd_toggle(self):
        await self.refresh()
        if self.state.get("status") == "connected":
            return await self.cmd_disconnect()
        return await self.cmd_connect()

    def _require_control(self):
        if self.aap is None or self.aap.closed or not self.state.get("control"):
            raise CommandError("AirPods are not connected")

    async def cmd_mode(self, mode: str):
        dev = await self._device()
        self._require_control()
        modes = dev.get("modes") or ADAPTIVE
        if mode == "cycle":
            current = self.state.get("noiseMode")
            # Cycle like the stem press: skip "off" when other modes exist.
            cycle = [m for m in modes if m != "off"] or modes
            mode = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else cycle[0]
        if mode not in NOISE_CODES:
            raise CommandError(f"unknown mode {mode!r}")
        if mode not in modes:
            raise CommandError(f"{dev['model']} doesn't support {mode}")
        self.aap.send(control_packet(CTL_NOISE_MODE, NOISE_CODES[mode]))
        self.publish(noiseMode=mode)
        return mode

    async def cmd_conversational(self, state: str = "toggle"):
        self._require_control()
        on = {"on": True, "off": False}.get(state, not self.state.get("conversationalAwareness"))
        self.aap.send(control_packet(CTL_CONVERSATIONAL, 0x01 if on else 0x02))
        self.publish(conversationalAwareness=on)
        return "on" if on else "off"

    async def cmd_autopause(self, state: str = "toggle"):
        on = {"on": True, "off": False}.get(state, not self.config.get("autoPause"))
        self.config["autoPause"] = on
        save_config(self.config)
        self.publish(autoPause=on)
        return "on" if on else "off"

    async def cmd_pair(self):
        if self.pair_task and not self.pair_task.done():
            return "already pairing"
        self.pair_task = asyncio.create_task(self._pair())
        return "put the AirPods in pairing mode"

    async def cmd_pair_cancel(self):
        if self.pair_task:
            self.pair_task.cancel()
        self.publish(pairing=None)
        return "cancelled"

    async def _pair(self):
        """Scan for AirPods in pairing mode, then pair, trust and connect."""
        self.publish(pairing={"stage": "scanning"})
        scan = await asyncio.create_subprocess_exec(
            "bluetoothctl", "--timeout", "60", "scan", "on",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            paired = {mac for mac, _ in await bt_devices("Paired")}
            target = None
            for _ in range(60):
                for mac, name in await bt_devices():
                    if mac not in paired and "airpods" in name.lower():
                        target = (mac, name)
                        break
                if target:
                    break
                await asyncio.sleep(1)
            if not target:
                self.publish(pairing={"stage": "failed", "error": "No AirPods found in pairing mode"})
                return
            mac, name = target
            self.publish(pairing={"stage": "pairing", "name": name})
            out = await run("bluetoothctl", "pair", mac, timeout=30)
            if "Pairing successful" not in out and "AlreadyExists" not in out:
                raise CommandError((out.strip().splitlines() or ["pairing failed"])[-1])
            await run("bluetoothctl", "trust", mac, timeout=5)
            await run("bluetoothctl", "connect", mac, timeout=20)
            self.config["mac"] = mac
            save_config(self.config)
            self.publish(pairing=None)
            await self.refresh()
        except CommandError as exc:
            self.publish(pairing={"stage": "failed", "error": str(exc)})
        finally:
            if scan.returncode is None:
                scan.terminate()
            await run("bluetoothctl", "scan", "off", timeout=5)


# ---- daemon ----------------------------------------------------------------


async def daemon():
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "daemon.lock"), "w")
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            await asyncio.sleep(1)

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    ctl = Controller(on_state=lambda s: emit({"type": "state", "state": s}))

    async def dispatch(msg):
        try:
            return {"type": "result", "id": msg.get("id"), "ok": True,
                    "result": await ctl.handle(msg)}
        except Exception as exc:
            return {"type": "result", "id": msg.get("id"), "ok": False,
                    "error": str(exc) or type(exc).__name__}

    async def serve_client(reader, writer):
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                writer.write((json.dumps(await dispatch(msg)) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()

    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(serve_client, path=SOCKET)
    os.chmod(SOCKET, 0o600)

    reader = asyncio.StreamReader()
    await ctl.loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    try:
        await ctl.refresh()
        watcher = asyncio.create_task(ctl.watch_bluez())
        # Exit when the shell closes our stdin (shell restart / plugin unload).
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            asyncio.create_task(dispatch(msg)).add_done_callback(lambda t: emit(t.result()))
        watcher.cancel()
    finally:
        server.close()
        ctl._stop_aap()
        try:
            os.unlink(SOCKET)
        except FileNotFoundError:
            pass


# ---- CLI -------------------------------------------------------------------


async def send(msg: dict):
    try:
        reader, writer = await asyncio.open_unix_connection(SOCKET)
    except (FileNotFoundError, ConnectionRefusedError):
        if msg["cmd"] in ("mode", "conversational"):
            raise CommandError("the airpods daemon isn't running (enable the bar widget)")
        return await Controller().handle(msg)
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()
    res = json.loads(await reader.readline())
    writer.close()
    if not res.get("ok"):
        raise CommandError(res.get("error"))
    return res.get("result")


def main(argv):
    p = argparse.ArgumentParser(prog="airpods", description="Control AirPods from Omarchy")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="run the background session (used by the shell)")
    sub.add_parser("status", help="print state as JSON")
    sub.add_parser("connect")
    sub.add_parser("disconnect")
    sub.add_parser("toggle", help="connect or disconnect")
    s = sub.add_parser("mode", help="noise control")
    s.add_argument("mode", choices=["off", "transparency", "adaptive", "anc", "cycle"])
    s = sub.add_parser("conversational", help="conversational awareness")
    s.add_argument("state", choices=["on", "off", "toggle"], nargs="?", default="toggle")
    s = sub.add_parser("autopause", help="pause media when an AirPod is removed")
    s.add_argument("state", choices=["on", "off", "toggle"], nargs="?", default="toggle")
    sub.add_parser("pair", help="find AirPods in pairing mode and pair them")

    a = p.parse_args(argv)
    if a.command == "daemon":
        return asyncio.run(daemon())

    msg = {"cmd": a.command}
    if a.command == "mode":
        msg["mode"] = a.mode
    elif a.command in ("conversational", "autopause"):
        msg["state"] = a.state

    async def go():
        if a.command != "pair":
            return await send(msg)
        # Pairing runs in the background; follow it until it finishes.
        ctl = Controller(on_state=lambda s: None)
        print("Open the case lid, then hold the button on the back until the light flashes white…")
        await ctl.handle(msg)
        await ctl.pair_task
        pairing = ctl.state.get("pairing")
        if pairing:
            raise CommandError(pairing.get("error", "pairing failed"))
        return f"paired {ctl.state['device']['name']}"

    try:
        result = asyncio.run(go())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # report, never traceback
        print(f"airpods: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
