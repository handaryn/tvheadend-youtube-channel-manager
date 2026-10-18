#!/usr/bin/env python3
"""
TVHeadend YouTube Channel Manager Service (Hardened & Multi-threaded)
Provides REST API endpoints for Kodi addon to search, add, list, and delete YouTube channels in TVHeadend.
"""

import http.server
import json
import os
import re
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import requests
from requests.auth import HTTPDigestAuth

CONFIG_PATH = os.environ.get("TVH_YT_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

def load_config():
    cfg = {
        "port": 9985,
        "tvh_host": "http://localhost:9981",
        "tvh_user": "",
        "tvh_pass": "",
        "network_uuid": "",
        "tag_uuid": "",
        "picons_dir": "/home/tvheadend/picons",
        "picons_tvh_url": "file:///config/picons",
        "db_file": os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels_db.json"),
        "pipe_script_path": "/config/list/scripts/youtube.sh"
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                cfg.update(user_cfg)
        except Exception as e:
            print(f"Warning: Failed to load config from {CONFIG_PATH}: {e}")
            
    # Allow environment variable overrides
    cfg["port"] = int(os.environ.get("TVH_YT_PORT", cfg["port"]))
    cfg["tvh_host"] = os.environ.get("TVH_HOST", cfg["tvh_host"])
    cfg["tvh_user"] = os.environ.get("TVH_USER", cfg["tvh_user"])
    cfg["tvh_pass"] = os.environ.get("TVH_PASS", cfg["tvh_pass"])
    cfg["network_uuid"] = os.environ.get("TVH_YT_NETWORK_UUID", cfg["network_uuid"])
    cfg["tag_uuid"] = os.environ.get("TVH_YT_TAG_UUID", cfg["tag_uuid"])
    cfg["picons_dir"] = os.environ.get("TVH_PICONS_DIR", cfg["picons_dir"])
    cfg["picons_tvh_url"] = os.environ.get("TVH_PICONS_TVH_URL", cfg["picons_tvh_url"])
    cfg["db_file"] = os.environ.get("TVH_YT_DB_FILE", cfg["db_file"])
    cfg["pipe_script_path"] = os.environ.get("TVH_YT_PIPE_SCRIPT", cfg["pipe_script_path"])
    return cfg

CONFIG = load_config()
PORT = CONFIG["port"]
TVH_HOST = CONFIG["tvh_host"]
TVH_CREDS = (CONFIG["tvh_user"], CONFIG["tvh_pass"])
NETWORK_UUID = CONFIG["network_uuid"]
TAG_UUID = CONFIG["tag_uuid"]
PICONS_DIR = CONFIG["picons_dir"]
PICONS_TVH_URL = CONFIG["picons_tvh_url"]
DB_FILE = CONFIG["db_file"]
PIPE_SCRIPT_PATH = CONFIG["pipe_script_path"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Cookie": "SOCS=CAESEwgDEgk2OTg5OTY0NTQaAmVuIAEaBgiA_LyaBg;",
    "Accept-Language": "en-US,en;q=0.9"
}

CHANNEL_ID_REGEX = re.compile(r"^UC[a-zA-Z0-9_-]{22}\Z")
UUID_REGEX = re.compile(r"^[0-9a-f]{32}\Z")
TRUSTED_AVATAR_HOSTS = ("googleusercontent.com", "ggpht.com", "ytimg.com")

db_lock = threading.Lock()

def tvh_api_request(method, endpoint, **kwargs):
    """Executes a TVHeadend API call with automatic Basic / Digest auth fallback."""
    url = f"{TVH_HOST}{endpoint}"
    kwargs.setdefault("timeout", 10)
    
    # 1. Try Basic Auth
    try:
        r = requests.request(method, url, auth=TVH_CREDS, **kwargs)
        if r.status_code != 401:
            return r
    except requests.RequestException:
        pass

    # 2. Fallback to Digest Auth
    try:
        digest_auth = HTTPDigestAuth(TVH_CREDS[0], TVH_CREDS[1])
        return requests.request(method, url, auth=digest_auth, **kwargs)
    except requests.RequestException as e:
        print(f"TVHeadend request error: {e}")
        return None

def load_db():
    with db_lock:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

def update_db(modifier_fn):
    """Atomically load, modify, and save the database under lock."""
    with db_lock:
        db = {}
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    db = json.load(f)
            except Exception:
                db = {}
        result = modifier_fn(db)
        try:
            db_dir = os.path.dirname(os.path.abspath(DB_FILE))
            os.makedirs(db_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=db_dir, delete=False, encoding="utf-8") as tf:
                json.dump(db, tf, indent=2)
                temp_name = tf.name
            os.replace(temp_name, os.path.abspath(DB_FILE))
        except Exception as e:
            print(f"Error saving DB: {e}")
        return result

def search_youtube_channels(query):
    encoded = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAg%253D%253D"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")
    
    m = re.search(r"var ytInitialData = ({.*?});</script>", html) or re.search(r"window\[\"ytInitialData\"\] = ({.*?});</script>", html)
        
    results = []
    if m:
        try:
            data = json.loads(m.group(1))
            items = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"][0]["itemSectionRenderer"]["contents"]
            for it in items:
                cr = it.get("channelRenderer")
                if cr:
                    title = cr.get("title", {}).get("simpleText", "")
                    ch_id = cr.get("channelId", "")
                    if not CHANNEL_ID_REGEX.match(ch_id):
                        continue
                    
                    nav = cr.get("navigationEndpoint", {}).get("browseEndpoint", {})
                    canonical = nav.get("canonicalBaseUrl", f"/channel/{ch_id}")
                    full_channel_url = f"https://www.youtube.com{canonical}" if not canonical.startswith("http") else canonical
                    
                    thumbnails = cr.get("thumbnail", {}).get("thumbnails", [])
                    avatar = ""
                    if thumbnails:
                        avatar = thumbnails[-1].get("url", "")
                        if avatar.startswith("//"):
                            avatar = "https:" + avatar
                        # Upgrade resolution parameter to 512 for high fidelity
                        avatar = re.sub(r"=s\d+", "=s512", avatar)
                    
                    snippets = cr.get("descriptionSnippet", {}).get("runs", [])
                    desc = "".join([s.get("text", "") for s in snippets])
                    subscriber_count = cr.get("videoCountText", {}).get("simpleText", "")
                    
                    results.append({
                        "title": title,
                        "channel_id": ch_id,
                        "url": full_channel_url,
                        "live_url": f"https://www.youtube.com/channel/{ch_id}/live",
                        "avatar": avatar,
                        "description": desc or subscriber_count
                    })
        except Exception as e:
            print("Error parsing search results:", e)
    return results

def download_and_save_picon(avatar_url, channel_id):
    if not avatar_url or not channel_id:
        return None
        
    # Security: Validate avatar URL scheme and exact hostname/domain boundary
    parsed = urllib.parse.urlparse(avatar_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in TRUSTED_AVATAR_HOSTS):
        print(f"Rejected untrusted avatar URL host: {parsed.netloc}")
        return None

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".download")
    os.close(tmp_fd)
    dest_path = os.path.join(PICONS_DIR, f"{channel_id}.png")
    
    try:
        os.makedirs(PICONS_DIR, exist_ok=True)
        req = urllib.request.Request(avatar_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r, open(tmp_path, "wb") as f:
            # Enforce 10MB maximum download limit
            f.write(r.read(10 * 1024 * 1024))
            
        # Convert to pristine 500x500 PNG with RGBA transparency padding
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp_path,
            "-vf", "format=rgba,scale=500:500:force_original_aspect_ratio=decrease,pad=500:500:(ow-iw)/2:(oh-ih)/2:color=black@0",
            dest_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        os.chmod(dest_path, 0o644)
        return f"{channel_id}.png"
    except Exception as e:
        print(f"Error saving picon for {channel_id}: {e}")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def add_channel_to_tvheadend(channel_name, channel_id, avatar_url):
    if not isinstance(channel_id, str) or not isinstance(channel_name, str):
        return {"status": "error", "message": "Invalid parameter types"}
    if not CHANNEL_ID_REGEX.match(channel_id):
        return {"status": "error", "message": "Invalid YouTube channel ID"}
    if not NETWORK_UUID:
        return {"status": "error", "message": "NETWORK_UUID not configured in backend"}
        
    db = load_db()
    if channel_id in db:
        return {"status": "exists", "message": "Channel is already in TVHeadend", "channel_name": channel_name}
        
    safe_live_url = f"https://www.youtube.com/channel/{channel_id}/live"
    picon_filename = download_and_save_picon(avatar_url, channel_id)
    icon_url = f"{PICONS_TVH_URL}/{picon_filename}" if picon_filename else None
    
    pipe_url = f'pipe://{PIPE_SCRIPT_PATH} "{safe_live_url}"'
    mux_conf = {
        "enabled": 1,
        "epg": 0,
        "name": channel_name,
        "iptv_muxname": channel_name,
        "iptv_sname": channel_name,
        "iptv_url": pipe_url,
        "iptv_interface": "",
        "iptv_atsc": 0,
        "iptv_svcname": "Service01",
        "scan_state": 0
    }
    
    r_mux = tvh_api_request("POST", "/api/mpegts/network/mux_create", data={"uuid": NETWORK_UUID, "conf": json.dumps(mux_conf)})
    if not r_mux or r_mux.status_code != 200:
        return {"status": "error", "message": "Failed to create multiplex in TVHeadend"}
    
    mux_uuid = None
    try:
        mux_uuid = r_mux.json().get("uuid")
    except Exception:
        pass
        
    if not mux_uuid:
        return {"status": "error", "message": "TVHeadend did not return a mux UUID"}
        
    # Atomically record as provisioning in DB so reconciler does not race
    def _add_to_db(d):
        d[channel_id] = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "live_url": safe_live_url,
            "picon": picon_filename,
            "icon_url": icon_url,
            "mux_uuid": mux_uuid,
            "service_uuid": None,
            "channel_uuid": None,
            "status": "provisioning"
        }
    update_db(_add_to_db)
    
    # Check if stream is actively live right now
    service_uuid = None
    for _ in range(5):
        time.sleep(1)
        r_srv = tvh_api_request("GET", "/api/mpegts/service/grid?limit=1000")
        if r_srv and r_srv.status_code == 200:
            for s in r_srv.json().get("entries", []):
                if (mux_uuid and s.get("multiplex_uuid") == mux_uuid) or (s.get("multiplex") == channel_name and s.get("network") == "Youtube"):
                    service_uuid = s.get("uuid")
                    break
        if service_uuid:
            break
            
    if service_uuid:
        chan_conf = {
            "name": channel_name,
            "services": [service_uuid],
            "tags": [TAG_UUID] if TAG_UUID else [],
            "icon": icon_url
        }
        r_chan = tvh_api_request("POST", "/api/channel/create", data={"conf": json.dumps(chan_conf)})
        chan_uuid = r_chan.json().get("uuid") if (r_chan and r_chan.status_code == 200) else None
        
        def _mark_active(d):
            if channel_id in d:
                d[channel_id]["service_uuid"] = service_uuid
                d[channel_id]["channel_uuid"] = chan_uuid
                d[channel_id]["status"] = "active"
        update_db(_mark_active)
            
        return {"status": "ok", "channel_name": channel_name, "service_uuid": service_uuid, "icon": icon_url}
    else:
        def _mark_pending(d):
            if channel_id in d:
                d[channel_id]["status"] = "pending_scan"
        update_db(_mark_pending)
        return {"status": "pending", "message": "Stream is currently off-air. Channel will automatically map as soon as it goes live.", "channel_name": channel_name}

def reconcile_pending_channels():
    """Background loop to map channels once their live stream starts"""
    while True:
        try:
            db = load_db()
            pending = [ch for ch in db.values() if ch.get("status") == "pending_scan" and ch.get("mux_uuid")]
            if pending:
                r_srv = tvh_api_request("GET", "/api/mpegts/service/grid?limit=1000")
                if r_srv and r_srv.status_code == 200:
                    srvs = r_srv.json().get("entries", [])
                    for ch in pending:
                        ch_id = ch.get("channel_id")
                        cname = ch.get("channel_name")
                        mux_id = ch.get("mux_uuid")
                        match_srv = next((s for s in srvs if (mux_id and s.get("multiplex_uuid") == mux_id) or (s.get("multiplex") == cname and s.get("network") == "Youtube")), None)
                        if match_srv:
                            srv_uuid = match_srv.get("uuid")
                            chan_conf = {
                                "name": cname,
                                "services": [srv_uuid],
                                "tags": [TAG_UUID] if TAG_UUID else [],
                                "icon": ch.get("icon_url")
                            }
                            r_c = tvh_api_request("POST", "/api/channel/create", data={"conf": json.dumps(chan_conf)})
                            if r_c and r_c.status_code == 200:
                                chan_uuid = r_c.json().get("uuid")
                                if chan_uuid:
                                    def _update(d):
                                        if ch_id in d:
                                            d[ch_id]["service_uuid"] = srv_uuid
                                            d[ch_id]["channel_uuid"] = chan_uuid
                                            d[ch_id]["status"] = "active"
                                    update_db(_update)
                                    print(f"Reconciled and mapped channel: {cname}")
        except Exception as e:
            print(f"Reconcile error: {e}")
        time.sleep(30)

def list_youtube_channels(host_header="localhost:9985"):
    r = tvh_api_request("GET", "/api/channel/grid?limit=1000")
    channels = []
    if not r or r.status_code != 200:
        return channels
        
    for c in r.json().get("entries", []):
        tags = c.get("tags", [])
        if (TAG_UUID and TAG_UUID in tags) or "Youtube" in c.get("tags_str", ""):
            icon_file = os.path.basename(str(c.get("icon", "")))
            channels.append({
                "uuid": c.get("uuid"),
                "name": c.get("name"),
                "number": c.get("number"),
                "icon": c.get("icon"),
                "picon_filename": icon_file,
                "icon_public_url": f"http://{host_header}/picons/{icon_file}" if icon_file else ""
            })
    channels.sort(key=lambda x: str(x.get("name")).lower())
    return channels

def delete_youtube_channel(channel_uuid):
    if not TAG_UUID:
        return {"status": "error", "message": "Security error: TAG_UUID is not configured in backend"}
    if not UUID_REGEX.match(channel_uuid):
        return {"status": "error", "message": "Invalid channel UUID format"}

    r_chan = tvh_api_request("GET", f"/api/idnode/load?uuid={channel_uuid}")
    if not r_chan or r_chan.status_code != 200:
        return {"status": "error", "message": "Channel not found"}
        
    entries = r_chan.json().get("entries", [])
    if not entries:
        return {"status": "error", "message": "Item not found"}
        
    props = {p.get("id"): p.get("value") for p in entries[0].get("params", [])}
    tags = props.get("tags", [])
    if TAG_UUID not in tags and "Youtube" not in str(tags):
        return {"status": "error", "message": "Permission denied: Target is not a YouTube channel"}
        
    # Find associated services and muxes
    services = props.get("services", [])
    mux_uuids = set()
    for s_uuid in services:
        if not UUID_REGEX.match(s_uuid):
            continue
        r_s = tvh_api_request("GET", f"/api/idnode/load?uuid={s_uuid}")
        if r_s and r_s.status_code == 200:
            s_entries = r_s.json().get("entries", [])
            if s_entries:
                s_props = {p.get("id"): p.get("value") for p in s_entries[0].get("params", [])}
                m_uuid = s_props.get("multiplex_uuid")
                if m_uuid and UUID_REGEX.match(m_uuid):
                    mux_uuids.add(m_uuid)

    # 1. Delete Channel
    tvh_api_request("POST", "/api/idnode/delete", data={"uuid": channel_uuid})
    
    # 2. Delete Muxes
    for m_uuid in mux_uuids:
        tvh_api_request("POST", "/api/idnode/delete", data={"uuid": m_uuid})
        
    # 3. Clean up DB and Picon
    def _remove_from_db(d):
        for ch_id, ch in list(d.items()):
            if ch.get("channel_uuid") == channel_uuid or ch.get("mux_uuid") in mux_uuids:
                picon = ch.get("picon")
                if picon:
                    p_path = os.path.join(PICONS_DIR, picon)
                    if os.path.exists(p_path):
                        try:
                            os.remove(p_path)
                        except Exception:
                            pass
                del d[ch_id]
                break
    update_db(_remove_from_db)
            
    return {"status": "deleted", "uuid": channel_uuid}

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def send_json(self, status_code, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        host_header = self.headers.get("Host", f"localhost:{PORT}")
        
        try:
            if parsed.path == "/api/search":
                q = params.get("q", [""])[0]
                results = search_youtube_channels(q) if q else []
                self.send_json(200, results)
                
            elif parsed.path == "/api/list":
                channels = list_youtube_channels(host_header)
                self.send_json(200, channels)
                
            elif parsed.path == "/api/status":
                self.send_json(200, {"status": "running", "network": "Youtube"})
                
            elif parsed.path.startswith("/picons/"):
                filename = os.path.basename(parsed.path)
                picon_path = os.path.join(PICONS_DIR, filename)
                if os.path.exists(picon_path) and filename.endswith(".png"):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(os.path.getsize(picon_path)))
                    self.end_headers()
                    with open(picon_path, "rb") as f:
                        self.wfile.write(f.read())
                else:
                    self.send_response(404)
                    self.end_headers()
                    
            elif parsed.path == "/download/addon.zip":
                possible_paths = [
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.video.tvh.youtube.channels.zip"),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kodi-addon", "plugin.video.tvh.youtube.channels.zip"),
                ]
                zip_path = next((p for p in possible_paths if os.path.exists(p)), None)
                if zip_path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", 'attachment; filename="plugin.video.tvh.youtube.channels.zip"')
                    self.send_header("Content-Length", str(os.path.getsize(zip_path)))
                    self.end_headers()
                    with open(zip_path, "rb") as f:
                        self.wfile.write(f.read())
                else:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            self.send_json(500, {"error": "Internal server error"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            length_hdr = self.headers.get("Content-Length", "0")
            length = int(length_hdr)
        except (ValueError, TypeError):
            self.send_json(400, {"error": "Invalid Content-Length header"})
            return

        if length < 0 or length > 1048576:  # 1MB max body limit
            self.send_json(413, {"error": "Payload size must be between 0 and 1MB"})
            return
            
        try:
            raw_body = self.rfile.read(length) if length else b"{}"
            body = raw_body.decode("utf-8", errors="replace")
            data = json.loads(body)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

        try:
            if parsed.path == "/api/add":
                name = data.get("name")
                channel_id = data.get("channel_id")
                avatar_url = data.get("avatar") or data.get("avatar_url")
                if not name or not channel_id:
                    self.send_json(400, {"error": "Missing name or channel_id"})
                    return
                res = add_channel_to_tvheadend(name, channel_id, avatar_url)
                self.send_json(200, res)
                
            elif parsed.path == "/api/delete":
                uuid = data.get("uuid")
                if not uuid:
                    self.send_json(400, {"error": "Missing uuid"})
                    return
                res = delete_youtube_channel(uuid)
                self.send_json(200, res)
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            self.send_json(500, {"error": "Internal server error"})

if __name__ == "__main__":
    t = threading.Thread(target=reconcile_pending_channels, daemon=True)
    t.start()
    
    server = ThreadedHTTPServer(("", PORT), RequestHandler)
    print(f"TVH YouTube Manager running on port {PORT}...")
    server.serve_forever()
