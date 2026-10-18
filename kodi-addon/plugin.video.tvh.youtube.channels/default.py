# -*- coding: utf-8 -*-
import sys
import json
import urllib.parse
import urllib.request
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

ADDON = xbmcaddon.Addon()
ADDON_HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != '-1' else 0
BASE_URL = sys.argv[0] if len(sys.argv) > 0 else ''

def get_api_base():
    host = ADDON.getSetting('server_host') or '127.0.0.1'
    port = ADDON.getSetting('server_port') or '9985'
    return f"http://{host}:{port}"

def http_get(endpoint):
    url = f"{get_api_base()}/{endpoint}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Kodi-TVH-Manager'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))

def http_post(endpoint, payload):
    url = f"{get_api_base()}/{endpoint}"
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Kodi-TVH-Manager'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def build_url(query):
    return f"{BASE_URL}?{urllib.parse.urlencode(query)}"

def main_menu():
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "TVHeadend YouTube Channels")
    xbmcplugin.setContent(ADDON_HANDLE, "videos")

    li_search = xbmcgui.ListItem(label="[COLOR yellow]🔍 Search & Add YouTube Channel[/COLOR]")
    li_search.setArt({'icon': 'DefaultAddonSearch.png'})
    li_search.setInfo('video', {'plot': 'Search YouTube for channels and add them directly into TVHeadend with custom icons and guide tags.'})
    xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=build_url({'action': 'search_dialog'}), listitem=li_search, isFolder=True)

    li_list = xbmcgui.ListItem(label="[COLOR green]📺 View Active YouTube Channels[/COLOR]")
    li_list.setArt({'icon': 'DefaultTVShows.png'})
    li_list.setInfo('video', {'plot': 'List all currently active YouTube channels in TVHeadend and remove any unwanted channels.'})
    xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=build_url({'action': 'list_active'}), listitem=li_list, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE)

def search_dialog():
    kb = xbmc.Keyboard('', 'Search YouTube Channel (e.g. Airport Action, Big Jet TV)')
    kb.doModal()
    if kb.isConfirmed() and kb.getText():
        query = kb.getText()
        show_search_results(query)
    else:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)

def show_search_results(query):
    xbmcplugin.setPluginCategory(ADDON_HANDLE, f"Results for: {query}")
    xbmcplugin.setContent(ADDON_HANDLE, "videos")

    dialog = xbmcgui.DialogProgress()
    dialog.create('Searching YouTube', f'Searching channels for "{query}"...')

    try:
        results = http_get(f"api/search?q={urllib.parse.quote(query)}")
    except Exception as e:
        dialog.close()
        xbmcgui.Dialog().ok("Connection Error", f"Could not connect to TVH Manager:\n{e}")
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return

    dialog.close()

    if not results:
        xbmcgui.Dialog().notification("Search", "No channels found", xbmcgui.NOTIFICATION_INFO, 3000)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return

    for item in results:
        title = item.get('title')
        desc = item.get('description', '')
        avatar = item.get('avatar', '')
        channel_id = item.get('channel_id', '')

        label = f"[COLOR cyan]{title}[/COLOR]"
        li = xbmcgui.ListItem(label=label)
        if avatar:
            li.setArt({'thumb': avatar, 'icon': avatar, 'poster': avatar})
        li.setInfo('video', {'title': title, 'plot': f"{desc}\n\nClick to add this channel to TVHeadend Guide!"})

        url = build_url({
            'action': 'add_channel',
            'name': title,
            'channel_id': channel_id,
            'avatar': avatar
        })
        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=url, listitem=li, isFolder=False)

    xbmcplugin.endOfDirectory(ADDON_HANDLE)

def add_channel(params):
    name = params.get('name', '')
    channel_id = params.get('channel_id', '')
    avatar = params.get('avatar', '')

    confirm = xbmcgui.Dialog().yesno(
        "Add Channel to TV Guide",
        f"Do you want to add:\n\n[B]{name}[/B]\n\nto TVHeadend and Kodi TV Guide?"
    )
    if not confirm:
        return

    dialog = xbmcgui.DialogProgress()
    dialog.create('Adding Channel', f'Configuring "{name}" in TVHeadend and saving icon...')
    dialog.update(50)

    try:
        res = http_post("api/add", {"name": name, "channel_id": channel_id, "avatar": avatar})
        dialog.close()
        status = res.get('status')
        if status == 'ok':
            xbmcgui.Dialog().ok("Channel Added!", f"[B]{name}[/B] was successfully added to TVHeadend!\n\nIt will now appear in your TV Guide under the 'Youtube' channel group.")
        elif status == 'pending':
            xbmcgui.Dialog().ok("Channel Configured (Off-Air)", f"[B]{name}[/B] was saved!\n\nThe stream is currently off-air. It will automatically activate in your TV Guide as soon as the broadcaster goes live.")
        elif status == 'exists':
            xbmcgui.Dialog().notification("TVH Manager", f"{name} is already in TV Guide", xbmcgui.NOTIFICATION_WARNING, 3000)
        else:
            xbmcgui.Dialog().ok("Error", f"Failed to add channel: {res.get('message', 'Unknown error')}")
    except Exception as e:
        dialog.close()
        xbmcgui.Dialog().ok("Error", f"Could not connect to TVH Manager:\n{e}")

def list_active():
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "Active YouTube Channels in TVHeadend")
    xbmcplugin.setContent(ADDON_HANDLE, "videos")

    try:
        channels = http_get("api/list")
    except Exception as e:
        xbmcgui.Dialog().ok("Error", f"Could not fetch channels from TVH:\n{e}")
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False)
        return

    if not channels:
        li = xbmcgui.ListItem(label="[No YouTube channels found in TVHeadend]")
        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url="", listitem=li, isFolder=False)
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
        return

    for c in channels:
        name = c.get('name')
        uuid = c.get('uuid')
        icon_url = c.get('icon_public_url') or "DefaultTVShows.png"

        li = xbmcgui.ListItem(label=f"🔴 {name}")
        li.setArt({'thumb': icon_url, 'icon': icon_url})
        li.setInfo('video', {'title': name, 'plot': f"Channel UUID: {uuid}\nClick to remove from TV Guide."})

        url = build_url({'action': 'delete_channel', 'uuid': uuid, 'name': name})
        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=url, listitem=li, isFolder=False)

    xbmcplugin.endOfDirectory(ADDON_HANDLE)

def delete_channel(params):
    uuid = params.get('uuid', '')
    name = params.get('name', '')

    confirm = xbmcgui.Dialog().yesno(
        "Remove Channel",
        f"Are you sure you want to remove [B]{name}[/B] from TVHeadend?"
    )
    if not confirm:
        return

    try:
        res = http_post("api/delete", {"uuid": uuid})
        if res.get('status') == 'deleted':
            xbmcgui.Dialog().notification("TVH Manager", f"Removed {name}", xbmcgui.NOTIFICATION_INFO, 3000)
            xbmc.executebuiltin("Container.Refresh")
        else:
            xbmcgui.Dialog().ok("Error", f"Failed: {res.get('message', 'Unknown error')}")
    except Exception as e:
        xbmcgui.Dialog().ok("Error", f"Failed to delete channel:\n{e}")

def router(paramstring):
    params = dict(urllib.parse.parse_qsl(paramstring))
    action = params.get('action')

    if not action:
        main_menu()
    elif action == 'search_dialog':
        search_dialog()
    elif action == 'add_channel':
        add_channel(params)
    elif action == 'list_active':
        list_active()
    elif action == 'delete_channel':
        delete_channel(params)

if __name__ == '__main__':
    router(sys.argv[2][1:] if len(sys.argv) > 2 else '')
