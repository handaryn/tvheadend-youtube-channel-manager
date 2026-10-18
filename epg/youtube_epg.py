#!/usr/bin/env python3
"""
YouTube Live Stream & Upcoming Broadcasts EPG Updater for TVHeadend
Strictly distinguishes truly active LIVE broadcasts from on-demand/ended VOD clips,
and injects XMLTV EPG data directly into TVHeadend.
"""

import os
import re
import gzip
import json
import socket
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.sax.saxutils as saxutils

TVH_DIR = os.environ.get("TVH_DIR", "/home/tvheadend")
SOCK_PATH = os.environ.get("TVH_XMLTV_SOCK", os.path.join(TVH_DIR, "epggrab/xmltv.sock"))
YT_NETWORK_UUID = os.environ.get("TVH_YT_NETWORK_UUID", "")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Cookie": "SOCS=CAESEwgDEgk2OTg5OTY0NTQaAmVuIAEaBgiA_LyaBg;",
    "Accept-Language": "en-US,en;q=0.9"
}

def search_key(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from search_key(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from search_key(item, key)

def get_channels_from_tvh():
    channels = {}
    mux_dirs = []
    if YT_NETWORK_UUID:
        p = os.path.join(TVH_DIR, f"input/iptv/networks/{YT_NETWORK_UUID}/muxes")
        if os.path.isdir(p):
            mux_dirs.append(p)
    else:
        networks_base = os.path.join(TVH_DIR, "input/iptv/networks")
        if os.path.isdir(networks_base):
            for net_uuid in os.listdir(networks_base):
                p = os.path.join(networks_base, net_uuid, "muxes")
                if os.path.isdir(p):
                    mux_dirs.append(p)

    if not mux_dirs:
        return channels
        
    ch_config_dir = os.path.join(TVH_DIR, "channel/config")
    service_to_chname = {}
    if os.path.isdir(ch_config_dir):
        for ch_file in os.listdir(ch_config_dir):
            try:
                with open(os.path.join(ch_config_dir, ch_file)) as f:
                    ch_data = json.load(f)
                    cname = ch_data.get("name")
                    if cname and not cname.startswith("Youtube/pipe:"):
                        for s in ch_data.get("services", []):
                            service_to_chname[s] = cname
            except Exception:
                pass
    
    for mux_dir in mux_dirs:
        for fname in os.listdir(mux_dir):
            p = os.path.join(mux_dir, fname)
            try:
                with open(p, "rb") as f:
                    data = f.read()
                idx = data.find(b"\x1f\x8b")
                if idx == -1:
                    continue
                text = gzip.decompress(data[idx:]).decode("utf-8", errors="ignore")
                if "youtube.com" not in text and "youtu.be" not in text:
                    continue
                m_url = re.search(r"https?://[a-zA-Z0-9_\-\.\/\?%&=+@]+", text)
                if not m_url:
                    continue
                url = m_url.group(0).strip()
            
                name = None
                m_name = re.search(r"iptv_muxname([^\x00-\x1f\x7f-\x9f]+)", text)
                if m_name:
                    name = m_name.group(1).strip()
                else:
                    m_svc = re.search(r"[0-9a-f]{32}", text)
                    if m_svc and m_svc.group(0) in service_to_chname:
                        name = service_to_chname[m_svc.group(0)]
                        
                if name:
                    channels[name] = url
            except Exception:
                continue
            
    # Match with TVHeadend channel configs for exact casing
    if os.path.isdir(ch_config_dir):
        for ch_file in os.listdir(ch_config_dir):
            try:
                with open(os.path.join(ch_config_dir, ch_file)) as f:
                    ch_data = json.load(f)
                    ch_name = ch_data.get("name")
                    if ch_name:
                        for k in list(channels.keys()):
                            if k.lower() == ch_name.lower():
                                url = channels.pop(k)
                                channels[ch_name] = url
            except Exception:
                pass
                
    return channels

def check_channel_status(name, url):
    check_urls = [url]
    if "@" in url:
        base_url = re.sub(r"/(live|featured|videos|streams)?$", "", url.strip())
        check_urls = [base_url + "/live", base_url + "/streams"]
    elif "youtube.com" in url and ("/channel/" in url or "/c/" in url):
        base_url = re.sub(r"/(live|featured|videos|streams)?$", "", url.strip())
        check_urls = [base_url + "/live", base_url + "/streams"]

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for u in check_urls:
        try:
            req = urllib.request.Request(u, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                content = resp.read().decode("utf-8", errors="ignore")

            # 1. Method A: Direct player on /live or watch URL
            m_pr = re.search(r"var ytInitialPlayerResponse = ({.*?});</script>", content) or re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});</script>", content) or re.search(r"var ytInitialPlayerResponse = ({.*?});", content)
            if m_pr:
                try:
                    data = json.loads(m_pr.group(1))
                    vd = data.get("videoDetails", {})
                    title = vd.get("title", "")
                    pmr = data.get("microformat", {}).get("playerMicroformatRenderer", {})
                    lbd = pmr.get("liveBroadcastDetails", {})
                    
                    if (vd.get("isLive", False) is True or lbd.get("isLiveNow", False) is True) and title:
                        return name, "live", title, None
                        
                    if (vd.get("isUpcoming", False) is True or ("startTimestamp" in lbd and not lbd.get("isLiveNow", False))) and title:
                        start_str = lbd.get("startTimestamp")
                        if start_str:
                            dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            if dt > now_utc:
                                return name, "upcoming", title, int(dt.timestamp())
                except Exception:
                    pass

            # 2. Method B: Search ytInitialData (/streams or channel tab) for lockupViewModels and legacy renderers
            m_data = re.search(r"var ytInitialData = ({.*?});</script>", content) or re.search(r">var ytInitialData = ({.*?});", content)
            if m_data:
                try:
                    data = json.loads(m_data.group(1))

                    # B1. Modern lockupViewModel (New YouTube layout)
                    for lvm in search_key(data, "lockupViewModel"):
                        thm = lvm.get("contentImage", {}).get("thumbnailViewModel", {})
                        badges = []
                        for o in thm.get("overlays", []):
                            for b in o.get("thumbnailBottomOverlayViewModel", {}).get("badges", []):
                                tbvm = b.get("thumbnailBadgeViewModel", {})
                                badges.append((tbvm.get("text", ""), tbvm.get("badgeStyle", "")))

                        meta = lvm.get("metadata", {}).get("lockupMetadataViewModel", {})
                        title = meta.get("title", {}).get("content") or lvm.get("rendererContext", {}).get("accessibilityContext", {}).get("label")
                        cid = lvm.get("contentId")

                        # Actively Live stream detected
                        if any(txt == "LIVE" or "LIVE" in style for txt, style in badges):
                            if title:
                                return name, "live", title, None

                        # Scheduled upcoming stream detected
                        if any(txt == "Upcoming" for txt, style in badges) and cid:
                            # Try to extract exact timestamp by resolving video page microformat
                            start_ts = None
                            try:
                                v_url = f"https://www.youtube.com/watch?v={cid}"
                                req_v = urllib.request.Request(v_url, headers=HEADERS)
                                with urllib.request.urlopen(req_v, timeout=5) as resp_v:
                                    v_content = resp_v.read().decode("utf-8", errors="ignore")
                                m_vpr = re.search(r"var ytInitialPlayerResponse = ({.*?});</script>", v_content) or re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});</script>", v_content) or re.search(r"var ytInitialPlayerResponse = ({.*?});", v_content)
                                if m_vpr:
                                    v_data = json.loads(m_vpr.group(1))
                                    v_lbd = v_data.get("microformat", {}).get("playerMicroformatRenderer", {}).get("liveBroadcastDetails", {})
                                    if "startTimestamp" in v_lbd:
                                        dt = datetime.datetime.fromisoformat(v_lbd["startTimestamp"].replace("Z", "+00:00"))
                                        if dt > now_utc:
                                            start_ts = int(dt.timestamp())
                            except Exception:
                                pass

                            if title and start_ts:
                                return name, "upcoming", title, start_ts

                    # B2. Legacy videoRenderer / gridVideoRenderer
                    for vr_key in ["videoRenderer", "gridVideoRenderer"]:
                        for vr in search_key(data, vr_key):
                            is_live = False
                            for b in vr.get("badges", []):
                                if b.get("metadataBadgeRenderer", {}).get("label") == "LIVE":
                                    is_live = True
                            for o in vr.get("thumbnailOverlays", []):
                                if o.get("thumbnailOverlayTimeStatusRenderer", {}).get("text", {}).get("simpleText") == "LIVE":
                                    is_live = True
                            if is_live:
                                title = vr.get("title", {}).get("runs", [{}])[0].get("text") or vr.get("title", {}).get("simpleText")
                                if title:
                                    return name, "live", title, None

                    # B3. Legacy upcomingEventData
                    def find_upcoming(obj):
                        if isinstance(obj, dict):
                            if "upcomingEventData" in obj and "startTime" in obj["upcomingEventData"]:
                                t = ""
                                if "title" in obj:
                                    runs = obj["title"].get("runs", [])
                                    if runs:
                                        t = runs[0].get("text", "")
                                    elif "simpleText" in obj["title"]:
                                        t = obj["title"]["simpleText"]
                                start_time = int(obj["upcomingEventData"]["startTime"])
                                return start_time, t
                            for v in obj.values():
                                res = find_upcoming(v)
                                if res:
                                    return res
                        elif isinstance(obj, list):
                            for item in obj:
                                res = find_upcoming(item)
                                if res:
                                    return res
                        return None

                    upcoming = find_upcoming(data)
                    if upcoming:
                        start_ts, up_title = upcoming
                        if start_ts > now_utc.timestamp():
                            return name, "upcoming", up_title, start_ts

                except Exception:
                    pass

        except Exception:
            pass

    # 3. Channel is not live (Off-Air)
    return name, "offair", None, None

def generate_xmltv(channel_results):
    now = datetime.datetime.now(datetime.timezone.utc)
    now_start = (now - datetime.timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")
    now_end = (now + datetime.timedelta(hours=6)).strftime("%Y%m%d%H%M%S +0000")
    
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        '<tv source-info-name="YouTube Live Stream EPG">'
    ]
    
    for name, status, title, start_ts in channel_results:
        esc_name = saxutils.escape(name, {'"': '&quot;'})
        lines.append(f'  <channel id="{esc_name}">')
        lines.append(f'    <display-name>{esc_name}</display-name>')
        lines.append('  </channel>')
        
        if status == "live" and title:
            clean_title = title.replace("🔴", "").replace("LIVE:", "").strip()
            prog_title = saxutils.escape(f"🔴 LIVE: {clean_title}")
            prog_desc = saxutils.escape(f"Currently broadcasting live on YouTube: {title}")
            lines.append(f'  <programme start="{now_start}" stop="{now_end}" channel="{esc_name}">')
            lines.append(f'    <title lang="en">{prog_title}</title>')
            lines.append(f'    <desc lang="en">{prog_desc}</desc>')
            lines.append('    <category lang="en">Special</category>')
            lines.append('  </programme>')
            
        elif status == "upcoming" and title and start_ts and (start_ts > now.timestamp()):
            dt_up = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc)
            up_start_str = dt_up.strftime("%Y%m%d%H%M%S +0000")
            up_end_str = (dt_up + datetime.timedelta(hours=4)).strftime("%Y%m%d%H%M%S +0000")
            time_display = dt_up.strftime("%a %H:%M UTC")
            
            clean_title = title.replace("⏳", "").replace("UPCOMING:", "").strip()
            
            # 1. Current block leading up to start
            offair_title = saxutils.escape(f"Off-Air (Next: ⏳ {time_display} - {clean_title})")
            offair_desc = saxutils.escape(f"Scheduled broadcast starts at {time_display}: {clean_title}")
            lines.append(f'  <programme start="{now_start}" stop="{up_start_str}" channel="{esc_name}">')
            lines.append(f'    <title lang="en">{offair_title}</title>')
            lines.append(f'    <desc lang="en">{offair_desc}</desc>')
            lines.append('    <category lang="en">Special</category>')
            lines.append('  </programme>')
            
            # 2. Scheduled upcoming block
            upcoming_title = saxutils.escape(f"⏳ UPCOMING: {clean_title} (Starts at {time_display})")
            upcoming_desc = saxutils.escape(f"Upcoming scheduled live broadcast starting at {time_display}: {clean_title}")
            lines.append(f'  <programme start="{up_start_str}" stop="{up_end_str}" channel="{esc_name}">')
            lines.append(f'    <title lang="en">{upcoming_title}</title>')
            lines.append(f'    <desc lang="en">{upcoming_desc}</desc>')
            lines.append('    <category lang="en">Special</category>')
            lines.append('  </programme>')
            
        else:
            prog_title = "Off-Air (Not Live)"
            prog_desc = saxutils.escape(f"{name} is not currently broadcasting live on YouTube.")
            lines.append(f'  <programme start="{now_start}" stop="{now_end}" channel="{esc_name}">')
            lines.append(f'    <title lang="en">{prog_title}</title>')
            lines.append(f'    <desc lang="en">{prog_desc}</desc>')
            lines.append('    <category lang="en">Special</category>')
            lines.append('  </programme>')
            
    lines.append('</tv>')
    return "\n".join(lines)

def send_to_tvh_socket(xml_content):
    if not os.path.exists(SOCK_PATH):
        raise FileNotFoundError(f"TVHeadend XMLTV socket not found at {SOCK_PATH}")
        
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCK_PATH)
        s.sendall(xml_content.encode("utf-8"))

def main():
    channels = get_channels_from_tvh()
    if not channels:
        print("No YouTube channels found in TVHeadend configuration.")
        return
        
    print(f"Checking {len(channels)} YouTube channels for true live and upcoming broadcasts...")
    results = []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(check_channel_status, name, url): name for name, url in channels.items()}
        for f in as_completed(futures):
            name, status, title, start_ts = f.result()
            results.append((name, status, title, start_ts))
            if status == "live":
                status_str = f"🔴 LIVE: {title}"
            elif status == "upcoming":
                dt_up = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc)
                status_str = f"⏳ UPCOMING ({dt_up.strftime('%a %H:%M UTC')}): {title}"
            else:
                status_str = "Off-Air"
            print(f"  - {name:28}: {status_str}")
            
    xml_data = generate_xmltv(results)
    send_to_tvh_socket(xml_data)
    print("✅ EPG updated and sent to TVHeadend successfully!")

if __name__ == '__main__':
    main()
