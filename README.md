# Wi-Fi Microscope Live Viewer

This repository contains a small launcher for the Wi-Fi microscope video stream.

The microscope sends raw Annex B H.264 over TCP port `8080`. It also uses short
UDP control/keepalive packets on ports `20000` and `20001`. `microscope_live.py`
sends the known startup and keepalive packets, then opens the TCP video stream
with `ffplay`.

## Requirements

- Python 3.10+
- FFmpeg with `ffplay` available on `PATH`
- Your computer connected to the microscope Wi-Fi network

## Run

```bash
uv run python microscope_live.py
```

By default the app connects to:

```text
192.168.34.1:8080
```

Useful options:

```bash
# Print more ffplay diagnostics.
uv run python microscope_live.py --loglevel info

# If your microscope has a different IP.
uv run python microscope_live.py --host 192.168.34.1

# If the stream starts without UDP control packets.
uv run python microscope_live.py --no-control

# If the OS chooses the wrong Wi-Fi interface for UDP control packets.
uv run python microscope_live.py --bind-ip 192.168.34.2

# See the ffplay command without running it.
uv run python microscope_live.py --print-command
```

## Direct ffplay Test

If UDP control is not needed, this is the minimal raw stream command:

```bash
ffplay -fflags nobuffer -flags low_delay -framedrop -sync ext \
  -analyzeduration 0 -probesize 32 -f h264 \
  tcp://192.168.34.1:8080?timeout=5000000
```

## Network Protocol Notes

Default addresses:

```text
Microscope:    192.168.34.1
Client Device: 192.168.34.2
```

The protocol is split into two parts:

- UDP binary control/discovery on ports `20000` and `20001`
- TCP video streaming on port `8080`

There is no evidence that the video uses RTSP, RTP, HTTP chunking, MJPEG, or a
container format such as MP4 or MPEG-TS. TCP port `8080` carries a raw H.264
elementary stream.

### UDP Control

The Client Device sends short binary UDP packets before and during video
playback. The payloads are not text commands, but many start with ASCII magic
values:

```text
JHCMD
FDWN
```

Known startup/control packets from the Client Device to the microscope:

```text
Client Device:any -> 192.168.34.1:20001
46 44 57 4e 01 00 01 00 01 00 00
ASCII prefix: FDWN

Client Device:any -> 192.168.34.1:20000
4a 48 43 4d 44 10 00
ASCII prefix: JHCMD

Client Device:any -> 192.168.34.1:20000
4a 48 43 4d 44 d0 01
ASCII prefix: JHCMD

Client Device:any -> 192.168.34.1:20001
46 44 57 4e 20 00 06 00 00 00
ASCII prefix: FDWN

Client Device:any -> 192.168.34.1:20001
46 44 57 4e 20 00 01 00 10 00 1a 05 10 09 28 3a
08 00 52 91 1a 05 10 11 28 3a
ASCII prefix: FDWN
```

Known keepalive/status polling from the Client Device:

```text
Client Device:any -> 192.168.34.1:20001
46 44 57 4e 00 00 01 00 00 00
ASCII prefix: FDWN
Approximate interval: 0.5s

Client Device:any -> 192.168.34.1:20000
4a 48 43 4d 44 d0 01
ASCII prefix: JHCMD
Approximate interval: 1.5s
```

Known microscope UDP replies:

```text
192.168.34.1:20001 -> Client Device:20001
46 44 57 4e 01 00 ff ff 00 00
ASCII prefix: FDWN

192.168.34.1:20000 -> 0.0.0.0:20000
4a 48 43 4d 44 20 00 ...
ASCII prefix: JHCMD
Contains device strings including:
E.WH0911
HD-Microscope-bf9e
```

The exact meaning of each field is still unknown. `microscope_live.py` does not
fully implement the control protocol; it sends the known startup sequence and
known keepalive packets often enough to keep the preview alive. The UDP command
analysis is incomplete. The device may support additional UDP commands for
functions such as LED brightness, snapshots, resolution changes, device
settings, or firmware/status queries.

### TCP Video

After the UDP startup burst, the Client Device opens:

```text
Client Device:any -> 192.168.34.1:8080
```

The Client Device sends no application payload on this TCP connection. The
microscope immediately sends video bytes back on the same connection.

The first bytes of the TCP payload are:

```text
00 00 00 01 67 64 40 1f ac 2c a8 0a 03 19
00 00 00 01 68 ee 30 c0 80
00 00 00 01 65 ...
```

This is Annex B H.264:

```text
00 00 00 01 67 ... = SPS
00 00 00 01 68 ... = PPS
00 00 00 01 65 ... = IDR slice
00 00 00 01 41 ... = non-IDR slice
00 00 00 01 01 ... = non-IDR slice
```

The TCP stream is:

```text
format: raw H.264 video
codec: H.264 / AVC
profile: High
resolution: 640x384
pixel format: yuv420p
```

Known NAL unit pattern:

```text
SPS             14
PPS             14
IDR slices      14
non-IDR slices 194
```

Because the stream is raw H.264, it has no container timestamps. A player must
guess or be told a frame rate. Known working playback options are around
`18.5 fps` to `25 fps`. For a live preview, `ffplay` can consume the TCP stream
directly with `-f h264`.
