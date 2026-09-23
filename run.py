import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("YOUTUBE_API_KEY")

def api_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def parse_time(ts_str):
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)

def get_channel_data(channel_ids):
    ids_str = ",".join(channel_ids)
    url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics,contentDetails&id={ids_str}&key={API_KEY}"
    res = api_get(url)
    channels = []
    for item in res.get("items", []):
        channels.append({
            "id": item["id"],
            "title": item["snippet"]["title"],
            "views": int(item["statistics"].get("viewCount", 0)),
            "subs": int(item["statistics"].get("subscriberCount", 0)),
            "videos": int(item["statistics"].get("videoCount", 0)),
            "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"]
        })
    return channels

def get_latest_videos(playlist_id, max_results=50):
    url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId={playlist_id}&maxResults={max_results}&key={API_KEY}"
    res = api_get(url)
    video_ids = [item["contentDetails"]["videoId"] for item in res.get("items", [])]
    if not video_ids:
        return []

    v_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={','.join(video_ids)}&key={API_KEY}"
    v_res = api_get(v_url)
    videos = []
    for item in v_res.get("items", []):
        pub_at = item["snippet"].get("publishedAt", "")
        videos.append({
            "id": item["id"],
            "title": item["snippet"]["title"],
            "published_at": pub_at,
            "views": int(item["statistics"].get("viewCount", 0)),
            "likes": int(item["statistics"].get("likeCount", 0)),
            "comments": int(item["statistics"].get("commentCount", 0))
        })
    return videos

def build_48h_sparkline(history, current_views):
    now = datetime.now(timezone.utc)
    hourly_views = [0] * 48
    
    if not history:
        return 0, 0, " " * 48

    t_1h = now - timedelta(hours=1)
    rec_1h = min(history, key=lambda x: abs(parse_time(x["t"]) - t_1h), default=None)
    v_60m = max(0, current_views - rec_1h["v"]) if rec_1h else 0

    t_48h = now - timedelta(hours=48)
    rec_48h = min(history, key=lambda x: abs(parse_time(x["t"]) - t_48h), default=None)
    v_48h = max(0, current_views - rec_48h["v"]) if rec_48h else 0

    for i in range(48):
        target_end = now - timedelta(hours=47 - i)
        target_start = target_end - timedelta(hours=1)
        r_start = min(history, key=lambda x: abs(parse_time(x["t"]) - target_start), default=None)
        r_end = min(history, key=lambda x: abs(parse_time(x["t"]) - target_end), default=None)
        if r_start and r_end and (parse_time(r_end["t"]) > parse_time(r_start["t"])):
            hourly_views[i] = max(0, r_end["v"] - r_start["v"])

    bars = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    max_h = max(hourly_views) if max(hourly_views) > 0 else 1
    sparkline = "".join(bars[min(8, int((val / max_h) * 8))] if val > 0 else " " for val in hourly_views)

    return v_60m, v_48h, sparkline

def prune_history(history):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=50)
    return [e for e in history if parse_time(e["t"]) >= cutoff]

def main():
    if not API_KEY or not os.path.exists("channels.txt"):
        return

    with open("channels.txt", "r", encoding="utf-8") as f:
        channel_ids = [line.strip() for line in f if line.strip()]

    db = {"channels": {}, "videos": {}, "history": {"channels": {}, "videos": {}}}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    db = loaded
                    if "history" not in db:
                        db["history"] = {"channels": {}, "videos": {}}
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_display = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    channels_data = get_channel_data(channel_ids)
    channels_summary = {}

    for ch in channels_data:
        ch_id = ch["id"]
        ch_hist = db["history"]["channels"].setdefault(ch_id, [])

        v_60m, v_48h, sparkline = build_48h_sparkline(ch_hist, ch["views"])
        ch_hist.append({"t": now_iso, "v": ch["views"]})
        db["history"]["channels"][ch_id] = prune_history(ch_hist)

        # Tính chỉ số vận hành kênh
        avg_views_per_video = int(ch["views"] / ch["videos"]) if ch["videos"] > 0 else 0

        videos = get_latest_videos(ch["uploads_playlist"], max_results=50)
        video_list = []

        for vid in videos:
            v_id = vid["id"]
            vid_hist = db["history"]["videos"].setdefault(v_id, [])

            vid_60m, vid_48h, v_spark = build_48h_sparkline(vid_hist, vid["views"])
            vid_hist.append({"t": now_iso, "v": vid["views"]})
            db["history"]["videos"][v_id] = prune_history(vid_hist)

            # Tính tuổi thọ video (theo ngày)
            pub_date = parse_time(vid["published_at"]) if vid["published_at"] else now
            days_old = max(1, (now - pub_date).days)
            views_per_day = int(vid["views"] / days_old)

            video_list.append({
                "id": v_id,
                "title": vid["title"],
                "views": vid["views"],
                "likes": vid["likes"],
                "comments": vid["comments"],
                "published_at": vid["published_at"],
                "days_old": days_old,
                "views_per_day": views_per_day,
                "v_60m": vid_60m,
                "v_48h": vid_48h,
                "sparkline": v_spark
            })

        # Sắp xếp video: nếu chưa có view 48h, xếp theo views tổng giảm dần
        video_list.sort(key=lambda x: (x["v_48h"], x["v_60m"], x["views"]), reverse=True)

        channels_summary[ch_id] = {
            "id": ch_id,
            "title": ch["title"],
            "views": ch["views"],
            "subs": ch["subs"],
            "videos_count": ch["videos"],
            "avg_views": avg_views_per_video,
            "v_60m": v_60m,
            "v_48h": v_48h,
            "sparkline": sparkline,
            "videos": video_list
        }

    output_data = {
        "updated_at": now_iso,
        "updated_at_display": now_display,
        "channels": channels_summary,
        "history": db["history"]
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False)

    sorted_channels = sorted(channels_summary.values(), key=lambda x: (x["v_48h"], x["v_60m"], x["views"]), reverse=True)
    md = [
        "# 📊 Báo Cáo View YouTube Realtime",
        f"*Cập nhật: `{now_display}`*\n",
        "## 1. Xếp Hạng Kênh",
        "| Top | Kênh | View 48 Giờ | 48 Cột Giờ | View 60p | Tổng Views | Subs | TB View/Video |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |"
    ]

    for idx, ch in enumerate(sorted_channels, 1):
        v48 = f"+{ch['v_48h']:,}" if ch['v_48h'] > 0 else "0"
        v60 = f"+{ch['v_60m']:,}" if ch['v_60m'] > 0 else "0"
        md.append(f"| #{idx} | **{ch['title']}** | **{v48}** | `{ch['sparkline']}` | `{v60}` | {ch['views']:,} | {ch['subs']:,} | {ch['avg_views']:,} |")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

if __name__ == "__main__":
    main()
