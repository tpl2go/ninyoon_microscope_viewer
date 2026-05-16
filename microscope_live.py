#!/usr/bin/env python3
"""Launch a live viewer for the Wi-Fi microscope raw H.264 stream."""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterable


DEFAULT_HOST = "192.168.34.1"
DEFAULT_VIDEO_PORT = 8080
JHCMD_PORT = 20000
FDWN_PORT = 20001


@dataclass(frozen=True)
class UdpCommand:
    port: int
    payload: bytes
    label: str


# Commands observed in microscope_packetdump.pcapng when the official iPad app
# starts the preview. The stream itself is raw Annex B H.264 over TCP/8080.
STARTUP_COMMANDS = (
    UdpCommand(FDWN_PORT, bytes.fromhex("46 44 57 4e 01 00 01 00 01 00 00"), "FDWN start"),
    UdpCommand(JHCMD_PORT, bytes.fromhex("4a 48 43 4d 44 10 00"), "JHCMD init"),
    UdpCommand(JHCMD_PORT, bytes.fromhex("4a 48 43 4d 44 d0 01"), "JHCMD stream"),
    UdpCommand(FDWN_PORT, bytes.fromhex("46 44 57 4e 20 00 06 00 00 00"), "FDWN mode"),
    UdpCommand(
        FDWN_PORT,
        bytes.fromhex(
            "46 44 57 4e 20 00 01 00 10 00 1a 05 10 09 28 3a "
            "08 00 52 91 1a 05 10 11 28 3a"
        ),
        "FDWN config",
    ),
)

FDWN_KEEPALIVE = UdpCommand(
    FDWN_PORT,
    bytes.fromhex("46 44 57 4e 00 00 01 00 00 00"),
    "FDWN keepalive",
)
JHCMD_KEEPALIVE = UdpCommand(
    JHCMD_PORT,
    bytes.fromhex("4a 48 43 4d 44 d0 01"),
    "JHCMD keepalive",
)


class ControlSender:
    def __init__(self, host: str, bind_ip: str | None = None, verbose: bool = False) -> None:
        self.host = host
        self.verbose = verbose
        self.stop_event = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if bind_ip:
            self.socket.bind((bind_ip, 0))
        self.socket.settimeout(0.2)
        self.thread = threading.Thread(target=self._run_keepalive, name="microscope-control", daemon=True)

    def close(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.socket.close()

    def send_startup(self) -> None:
        # The official app sends a short burst with some repeated packets. This
        # mirrors that behavior without requiring exact source ports.
        burst = (
            STARTUP_COMMANDS[0],
            STARTUP_COMMANDS[0],
            STARTUP_COMMANDS[1],
            STARTUP_COMMANDS[2],
            STARTUP_COMMANDS[2],
            STARTUP_COMMANDS[3],
            STARTUP_COMMANDS[4],
        )
        self._send_many(burst, delay=0.015)

    def start_keepalive(self) -> None:
        self.thread.start()

    def _send_many(self, commands: Iterable[UdpCommand], delay: float) -> None:
        for command in commands:
            self._send(command)
            if delay > 0:
                time.sleep(delay)

    def _send(self, command: UdpCommand) -> None:
        self.socket.sendto(command.payload, (self.host, command.port))
        if self.verbose:
            print(f"sent {command.label} to {self.host}:{command.port}", file=sys.stderr)

    def _run_keepalive(self) -> None:
        next_fdwn = 0.0
        next_jhcmd = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_fdwn:
                self._send(FDWN_KEEPALIVE)
                next_fdwn = now + 0.5
            if now >= next_jhcmd:
                self._send(JHCMD_KEEPALIVE)
                next_jhcmd = now + 1.5
            self.stop_event.wait(0.05)


def build_ffplay_command(args: argparse.Namespace) -> list[str]:
    ffplay = args.ffplay or shutil.which("ffplay")
    if not ffplay:
        raise RuntimeError("ffplay was not found. Install FFmpeg or pass --ffplay /path/to/ffplay.")

    timeout_us = max(1, int(args.tcp_timeout * 1_000_000))
    url = f"tcp://{args.host}:{args.video_port}?timeout={timeout_us}"
    title = f"Wi-Fi Microscope {args.host}:{args.video_port}"

    return [
        ffplay,
        "-hide_banner",
        "-loglevel",
        args.loglevel,
        "-window_title",
        title,
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-framedrop",
        "-sync",
        "ext",
        "-analyzeduration",
        "0",
        "-probesize",
        "32",
        "-f",
        "h264",
        "-i",
        url,
        *args.ffplay_arg,
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show the live raw H.264 stream from the Wi-Fi microscope.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"microscope IP address (default: {DEFAULT_HOST})")
    parser.add_argument("--video-port", type=int, default=DEFAULT_VIDEO_PORT, help="raw H.264 TCP port")
    parser.add_argument("--bind-ip", help="optional local interface IP to use for UDP control packets")
    parser.add_argument("--no-control", action="store_true", help="skip UDP startup/keepalive commands")
    parser.add_argument("--startup-delay", type=float, default=0.15, help="seconds to wait after UDP startup before TCP")
    parser.add_argument("--tcp-timeout", type=float, default=5.0, help="TCP connect/read timeout in seconds")
    parser.add_argument("--ffplay", help="path to ffplay")
    parser.add_argument(
        "--ffplay-arg",
        action="append",
        default=[],
        help="extra argument appended to ffplay; repeat for multiple arguments",
    )
    parser.add_argument(
        "--loglevel",
        default="warning",
        choices=("quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"),
        help="ffplay log level",
    )
    parser.add_argument("--verbose-control", action="store_true", help="print UDP control packets as they are sent")
    parser.add_argument("--print-command", action="store_true", help="print ffplay command and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        command = build_ffplay_command(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.print_command:
        print(" ".join(command))
        return 0

    control: ControlSender | None = None
    try:
        if not args.no_control:
            control = ControlSender(args.host, bind_ip=args.bind_ip, verbose=args.verbose_control)
            control.send_startup()
            control.start_keepalive()
            time.sleep(max(0.0, args.startup_delay))

        print(f"opening raw H.264 stream from {args.host}:{args.video_port}", file=sys.stderr)
        process = subprocess.Popen(command)
        return process.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if control:
            control.close()


if __name__ == "__main__":
    raise SystemExit(main())
