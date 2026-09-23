import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("YOUTUBE_API_KEY")

def api_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

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
        videos.append({
            "id": item["id"],
            "title": item["snippet"]["title"],
            "views": int(item["statistics"].get("viewCount", 0))
        })
    return videos

def build_48h_sparkline(history, current_views):
    """Tính view tăng của từng giờ trong 48 giờ qua và vẽ 48 cột Unicode"""
    now = datetime.now(timezone.utc)
    # Tạo 48 mốc thời gian (mỗi mốc 1 giờ)
    hourly_views = [0] * 48
    
    if not history:
        return 0, 0, " " * 48

    # Lấy mốc cách đây 1 giờ để tính view 60m
    t_1h = now - timedelta(hours=1)
    rec_1h = min(history, key=lambda x: abs(datetime.fromisoformat(x["t"]) - t_1h), default=None)
    v_60m = max(0, current_views - rec_1h["v"]) if rec_1h else 0

    # Lấy mốc cách đây 48 giờ để tính tổng 48h
    t_48h = now - timedelta(hours=48)
    rec_48h = min(history, key=lambda x: abs(datetime.fromisoformat(x["t"]) - t_48h), default=None)
    v_48h = max(0, current_views - rec_48h["v"]) if rec_48h else 0

    # Tính delta cho từng giờ trong 48 giờ
    # Giờ thứ i (i chạy từ 47 về 0, với 0 là giờ gần nhất)
    for i in range(48):
        target_end = now - timedelta(hours=47 - i)
        target_start = target_end - timedelta(hours=1)
        
        r_start = min(history, key=lambda x: abs(datetime.fromisoformat(x["t"]) - target_start), default=None)
        r_end = min(history, key=lambda x: abs(datetime.fromisoformat(x["t"]) - target_end), default=None)
        
        if r_start and r_end and (datetime.fromisoformat(r_end["t"]) > datetime.fromisoformat(r_start["t"])):
            hourly_views[i] = max(0, r_end["v"] - r_start["v"])

    # Vẽ biểu đồ ký tự:  ▂▃▄▅▆▇█
    bars = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    max_h = max(hourly_views) if max(hourly_views) > 0 else 1
    sparkline = "".join(bars[int((val / max_h) * 8)] if val > 0 else " " for val in hourly_views)

    return v_60m, v_48h, sparkline

def prune_history(history):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=50)
    return [e for e in history if datetime.fromisoformat(e["t"]) >= cutoff]

def main():
    if not API_KEY or not os.path.exists("channels.txt"):
        return

    with open("channels.txt", "r", encoding="utf-8") as f:
        channel_ids = [line.strip() for line in f if line.strip()]

    history_db = {"channels": {}, "videos": {}}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                history_db = json.load(f)
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_display = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    channels_data = get_channel_data(channel_ids)
    processed_channels = []

    for ch in channels_data:
        ch_id = ch["id"]
        ch_hist = history_db.setdefault("channels", {}).get(ch_id, [])

        v_60m, v_48h, sparkline = build_48h_sparkline(ch_hist, ch["views"])
        ch_hist.append({"t": now_iso, "v": ch["views"]})
        history_db["channels"][ch_id] = prune_history(ch_hist)

        videos = get_latest_videos(ch["uploads_playlist"], max_results=50)
        processed_videos = []

        for vid in videos:
            v_id = vid["id"]
            vid_hist = history_db.setdefault("videos", {}).get(v_id, [])

            vid_60m, vid_48h, v_spark = build_48h_sparkline(vid_hist, vid["views"])
            vid_hist.append({"t": now_iso, "v": vid["views"]})
            history_db["videos"][v_id] = prune_history(vid_hist)

            processed_videos.append({
                "id": v_id,
                "title": vid["title"],
                "views": vid["views"],
                "v_60m": vid_60m,
                "v_48h": vid_48h,
                "sparkline": v_spark
            })

        processed_videos.sort(key=lambda x: (x["v_48h"], x["v_60m"]), reverse=True)

        processed_channels.append({
            "title": ch["title"],
            "views": ch["views"],
            "v_60m": v_60m,
            "v_48h": v_48h,
            "sparkline": sparkline,
            "subs": ch["subs"],
            "videos": processed_videos
        })

    # Sắp xếp kênh theo View 48h cao nhất xuống thấp
    processed_channels.sort(key=lambda x: (x["v_48h"], x["v_60m"]), reverse=True)

    # Xuất Markdown README
    md = [
        "# 📊 Báo Cáo Realtime: View 48 Giờ (Chi tiết từng giờ) & 60 Phút",
        f"*Cập nhật: `{now_display}`*\n",
        "## 1. Xếp Hạng Kênh (Xếp theo View 48h)",
        "| Top | Kênh | View 48 Giờ | Biểu đồ 48 Cột Giờ | View 60p | Tổng Views | Subs |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: |"
    ]

    for idx, ch in enumerate(processed_channels, 1):
        v48 = f"+{ch['v_48h']:,}" if ch['v_48h'] > 0 else "0"
        v60 = f"+{ch['v_60m']:,}" if ch['v_60m'] > 0 else "0"
        md.append(f"| #{idx} | **{ch['title']}** | **{v48}** | `{ch['sparkline']}` | `{v60}` | {ch['views']:,} | {ch['subs']:,} |")

    md.append("\n## 2. Chi Tiết Video Từng Kênh (Đã xếp theo View 48h cao nhất)")
    for ch in processed_channels:
        md.append(f"\n<details><summary><b>▶ {ch['title']} ({len(ch['videos'])} video)</b></summary>\n")
        md.append("| Tiêu đề Video | View 48 Giờ | Biểu đồ 48 Cột Giờ | View 60p | Tổng Views |")
        md.append("| :--- | :---: | :---: | :---: | :---: |")
        for v in ch["videos"]:
            v48 = f"+{v['v_48h']:,}" if v['v_48h'] > 0 else "0"
            v60 = f"+{v['v_60m']:,}" if v['v_60m'] > 0 else "0"
            md.append(f"| [{v['title']}](https://youtu.be/{v['id']}) | **{v48}** | `{v['sparkline']}` | `{v60}` | {v['views']:,} |")
        md.append("\n</details>")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(history_db, f, ensure_ascii=False)

if __name__ == "__main__":
    main()
