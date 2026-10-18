#!/bin/bash

# $1 is the target YouTube URL passed from the Tvheadend Mux
TARGET_URL="$1"

if [ -z "$TARGET_URL" ]; then
    echo "Error: No YouTube URL provided to the script." >&2
    exit 1
fi

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# 1. Try resolving stream URL via streamlink (fastest, no rate limits, native HLS, highest quality)
STREAM_URL=$(streamlink --stream-url "$TARGET_URL" best 2>/dev/null)

# 2. Fallback to yt-dlp if streamlink did not return a valid URL
if [ -z "$STREAM_URL" ] || [[ ! "$STREAM_URL" =~ ^https?:// ]]; then
    COOKIES_ARG=""
    if [ -f "/config/list/scripts/cookies.txt" ]; then
        COOKIES_ARG="--cookies /config/list/scripts/cookies.txt"
    elif [ -f "/config/youtube_cookies.txt" ]; then
        COOKIES_ARG="--cookies /config/youtube_cookies.txt"
    elif [ -f "/config/cookies.txt" ]; then
        COOKIES_ARG="--cookies /config/cookies.txt"
    fi

    STREAM_URL=$(yt-dlp $COOKIES_ARG --user-agent "$UA" \
      --extractor-args "youtube:player_client=ios,android" \
      --match-filter "is_live" \
      --no-warnings --prefer-free-formats --no-playlist \
      -f "96/95/94/best[protocol=m3u8_native]/best" \
      -g "$TARGET_URL" 2>/dev/null)
fi

# Verify that a valid live stream URL was obtained
if [ -n "$STREAM_URL" ] && [[ "$STREAM_URL" =~ ^https?:// ]]; then
    exec ffmpeg -loglevel error -user_agent "$UA" \
      -reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 -reconnect_on_http_error 1 -reconnect_delay_max 5 \
      -probesize 1M -analyzeduration 1M \
      -fflags +genpts+igndts+discardcorrupt+nobuffer -flags +low_delay \
      -i "$STREAM_URL" \
      -map 0:v:0? -map 0:a:0? \
      -c:v copy -bsf:v h264_mp4toannexb \
      -c:a aac -b:a 192k -ar 48000 -ac 2 -af "aresample=async=1:min_hard_comp=0.100000:first_pts=0" \
      -avoid_negative_ts make_zero \
      -mpegts_flags resend_headers \
      -f mpegts pipe:1
fi

echo "Channel is currently off-air (not live)." >&2
exit 1
