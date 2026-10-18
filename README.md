# TVHeadend YouTube Channel Manager & Kodi Integration

A toolset to search, add, stream, and manage live YouTube channels inside TVHeadend and Kodi, featuring automated picon extraction, low-latency IPTV pipe streaming, and XMLTV Electronic Programme Guide (EPG) generation.

---

## Features

- **Kodi Integration:** Search YouTube channels by name, view channel details/avatars, and add or delete channels directly from Kodi without accessing the TVHeadend web interface.
- **Automated Picon Generation:** Extracts official high-resolution channel avatars, normalizes them via FFmpeg into 500x500 PNGs with RGBA transparency padding, and assigns them to TVHeadend channels.
- **Direct API Provisioning:** Uses TVHeadend's internal API (`/api/mpegts/network/mux_create` & `/api/channel/create`) to provision muxes and map services without triggering full network rescans.
- **Background Reconciler:** If an added YouTube channel is temporarily off-air, the manager records it as pending and automatically completes the service-to-channel mapping when the live stream begins broadcasting.
- **IPTV Pipe Streamer:** Wraps `streamlink`, `yt-dlp`, and `ffmpeg` to resolve native YouTube HLS streams with low latency, proper MPEG-TS timestamps, and resampled audio.
- **XMLTV EPG Grabber:** Distinguishes active live broadcasts from upcoming scheduled events using YouTube's `lockupViewModel` and consent cookie handling, piping XMLTV guide data directly into TVHeadend's UNIX socket (`xmltv.sock`).

---

## Architecture

```
                    ┌───────────────────────────┐
                    │        Kodi Addon         │
                    │ (Search, Add, List, Del)  │
                    └─────────────┬─────────────┘
                                  │ HTTP REST (Port 9985)
                                  ▼
                    ┌───────────────────────────┐
                    │    Backend Manager        │
                    │       (server.py)         │
                    └──────┬──────────────┬─────┘
       FFmpeg Picon        │              │ TVHeadend API (Basic/Digest Auth)
       Image Processing    │              │ (Add Mux, Channel, Services)
                           ▼              ▼
                    ┌────────────┐  ┌───────────────────────────┐
                    │ Picon Dir  │  │         TVHeadend         │
                    │  (PNG 500) │  │  (IPTV Automatic Network) │
                    └────────────┘  └─────────────┬─────────────┘
                                                  │ pipe:// stream
                                                  ▼
                    ┌───────────────────────────┐
                    │      Pipe Streamer        │
                    │       (youtube.sh)        │
                    │  streamlink/yt-dlp/ffmpeg │
                    └───────────────────────────┘
```

---

## Repository Layout

```text
tvheadend-youtube-channel-manager/
├── backend/
│   ├── server.py                   # Multi-threaded HTTP API & auto-reconciler
│   ├── config.example.json         # Configuration template
│   ├── requirements.txt            # Python dependencies (requests)
│   └── tvh-youtube-manager.service # Systemd service unit template
├── kodi-addon/
│   ├── build_zip.py                # Packager script to produce Kodi zip artifact
│   └── plugin.video.tvh.youtube.channels/ # Addon source code
│       ├── addon.xml
│       ├── default.py
│       ├── icon.png
│       └── resources/
├── epg/
│   ├── youtube_epg.py              # XMLTV generator & live stream status detector
│   └── youtube.sh                  # IPTV pipe script for TVHeadend
├── .gitignore
├── LICENSE
└── README.md
```

---

## Prerequisites

1. **TVHeadend** (4.2 or 4.3+, bare-metal or Docker).
2. **FFmpeg**, **yt-dlp**, and optionally **streamlink** installed in the environment where TVHeadend executes pipe commands:
   ```bash
   sudo apt-get install -y ffmpeg
   sudo pip3 install -U yt-dlp streamlink requests
   ```
   *(If running TVHeadend in Docker, ensure these utilities and `youtube.sh` are accessible inside the TVHeadend container).*
3. **Kodi** (Matrix 19, Nexus 20, Omega 21+).

---

## Installation & Setup

### 1. Configure the Pipe Streamer in TVHeadend

Copy `epg/youtube.sh` into your TVHeadend scripts directory (e.g. `/config/list/scripts/youtube.sh` or `/home/docker/scripts/youtube.sh`):

```bash
chmod +x epg/youtube.sh
```

Ensure the path specified in `config.json` (`pipe_script_path`) matches the path inside TVHeadend.

---

### 2. Configure TVHeadend Network, Tag & XMLTV Grabber

1. In the TVHeadend Web UI, go to **Configuration > DVB Inputs > Networks**.
2. Add a new **IPTV Automatic Network** named `Youtube`.
3. Note the Network UUID from the URL or by querying the API:
   ```bash
   curl -u username:password "http://localhost:9981/api/mpegts/network/grid"
   ```
4. Under **Configuration > Channel / EPG > Channel Tags**, create a tag named `Youtube` and note its UUID.
5. Under **Configuration > Channel / EPG > EPG Grabber Modules**, verify that **External: XMLTV** is enabled. This creates the UNIX domain socket at `/home/tvheadend/epggrab/xmltv.sock` (or container config path).

---

### 3. Deploy the Backend Manager

1. Copy `backend/config.example.json` to `backend/config.json`:
   ```bash
   cd backend
   cp config.example.json config.json
   ```
2. Edit `config.json` with your TVHeadend credentials, host, network UUID, and tag UUID:
   ```json
   {
     "port": 9985,
     "tvh_host": "http://localhost:9981",
     "tvh_user": "YOUR_TVH_USERNAME",
     "tvh_pass": "YOUR_TVH_PASSWORD",
     "network_uuid": "YOUR_NETWORK_UUID",
     "tag_uuid": "YOUR_TAG_UUID",
     "picons_dir": "/home/tvheadend/picons",
     "picons_tvh_url": "file:///config/picons",
     "db_file": "channels_db.json",
     "pipe_script_path": "/config/list/scripts/youtube.sh"
   }
   ```
3. Start the service directly or via systemd:
   ```bash
   python3 server.py
   ```
   *(Or edit and copy `tvh-youtube-manager.service` to `/etc/systemd/system/`, enable, and start).*

---

### 4. Build & Install the Kodi Add-on

1. Build the zip package from the repository:
   ```bash
   python3 kodi-addon/build_zip.py
   ```
2. Transfer `kodi-addon/plugin.video.tvh.youtube.channels.zip` to your Kodi device.
3. In Kodi, navigate to **Settings > Add-ons > Install from zip file**.
4. Select the zip file and confirm installation.
5. In the Add-on Settings, configure the **Backend Manager Host IP** (e.g. `192.168.1.100`) and Port (`9985`).

---

### 5. Setup Automated XMLTV EPG Grabber

Schedule `epg/youtube_epg.py` via `cron` to run periodically (e.g. every 15 minutes):

```bash
crontab -e
```

Add the entry:
```cron
*/15 * * * * /usr/bin/python3 /path/to/epg/youtube_epg.py >/var/log/youtube_epg.log 2>&1
```

---

## Backend REST API Reference

| Endpoint | Method | Query / Payload | Description |
| :--- | :--- | :--- | :--- |
| `/api/search` | `GET` | `?q=<search_query>` | Searches YouTube channels and returns channel names, IDs, URLs, and avatars. |
| `/api/add` | `POST` | `{"name": "...", "channel_id": "UC...", "avatar": "..."}` | Adds a channel to TVHeadend, generates picon, and creates mux. |
| `/api/list` | `GET` | — | Returns list of currently active YouTube channels in TVHeadend. |
| `/api/delete` | `POST` | `{"uuid": "<tvh_channel_uuid>"}` | Safely deletes channel, mux, and associated picon. |
| `/api/status` | `GET` | — | Healthcheck status of manager service. |
| `/picons/<file>` | `GET` | — | Serves processed picon images. |

---

## License

This project is licensed under the [GNU General Public License v2.0 or later (GPL-2.0-or-later)](LICENSE).
