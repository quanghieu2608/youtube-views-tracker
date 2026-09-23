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

def calculate_delta(history, current_views, target_minutes):
    """Tìm mốc gần với target_minutes trước để tính view tăng thêm"""
    if not history:
        return 0
    now = datetime.now(timezone.utc)
    target_time = now - timedelta(minutes=target_minutes)

    # Tìm bản ghi trong quá khứ gần với mốc target_time nhất
    closest_record = None
    min_diff = None
    for entry in history:
        try:
            entry_time = datetime.fromisoformat(entry["t"])
            diff = abs((entry_time - target_time).total_seconds())
            if min_diff is None or diff < min_diff:
                min_diff = diff
                closest_record = entry
        except Exception:
            continue

    if closest_record:
        delta = current_views - closest_record["v"]
        return max(0, delta)
    return 0

def prune_history(history, max_hours=49):
    """Xóa các bản ghi cũ hơn 48-49 tiếng để tối ưu dung lượng"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    pruned = []
    for entry in history:
        try:
            if datetime.fromisoformat(entry["t"]) >= cutoff:
                pruned.append(entry)
        except Exception:
            pass
    return pruned

def main():
    if not API_KEY or not os.path.exists("channels.txt"):
        print("Thiếu API Key hoặc channels.txt")
        return

    with open("channels.txt", "r", encoding="utf-8") as f:
        channel_ids = [line.strip() for line in f if line.strip()]

    # Đọc kho lịch sử Time-series cũ
    history_db = {"channels": {}, "videos": {}}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                history_db = json.load(f)
                if "channels" not in history_db:
                    history_db = {"channels": {}, "videos": {}}
        except Exception:
            history_db = {"channels": {}, "videos": {}}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_display = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    channels_data = get_channel_data(channel_ids)
    processed_channels = []

    for ch in channels_data:
        ch_id = ch["id"]
        ch_hist = history_db["channels"].get(ch_id, [])

        # Tính View 60 phút và View 48 giờ
        v_60m = calculate_delta(ch_hist, ch["views"], target_minutes=60)
        v_48h = calculate_delta(ch_hist, ch["views"], target_minutes=48 * 60)

        # Cập nhật lịch sử mốc mới
        ch_hist.append({"t": now_iso, "v": ch["views"]})
        history_db["channels"][ch_id] = prune_history(ch_hist)

        # Lấy video của kênh
        videos = get_latest_videos(ch["uploads_playlist"], max_results=50)
        processed_videos = []

        for vid in videos:
            v_id = vid["id"]
            vid_hist = history_db["videos"].get(v_id, [])

            vid_60m = calculate_delta(vid_hist, vid["views"], target_minutes=60)
            vid_48h = calculate_delta(vid_hist, vid["views"], target_minutes=48 * 60)

            vid_hist.append({"t": now_iso, "v": vid["views"]})
            history_db["videos"][v_id] = prune_history(vid_hist)

            processed_videos.append({
                "id": v_id,
                "title": vid["title"],
                "views": vid["views"],
                "v_60m": vid_60m,
                "v_48h": vid_48h
            })

        # Sắp xếp video: Video có View 60 phút cao nhất lên đầu (nếu bằng nhau thì xét View 48h)
        processed_videos.sort(key=lambda x: (x["v_60m"], x["v_48h"], x["views"]), reverse=True)

        processed_channels.append({
            "title": ch["title"],
            "views": ch["views"],
            "v_60m": v_60m,
            "v_48h": v_48h,
            "subs": ch["subs"],
            "videos_count": ch["videos"],
            "videos": processed_videos
        })

    # Sắp xếp kênh: Kênh có View 60 phút cao nhất lên đầu (nếu bằng nhau thì xét View 48h)
    processed_channels.sort(key=lambda x: (x["v_60m"], x["v_48h"], x["views"]), reverse=True)

    # Tạo bảng báo cáo Markdown
    md_lines = [
        "# 📊 Báo Cáo Realtime: View 60 Phút & View 48 Giờ",
        f"*Cập nhật lần cuối: `{now_display}`*\n",
        "> *(Bảng đã tự động sắp xếp theo thứ tự **View 60 phút cao nhất** xuống thấp)*\n",
        "## 1. Xếp Hạng Kênh",
        "| Top | Tên Kênh | View 60 phút | View 48 giờ | Tổng Views | Subs | Video |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: |"
    ]

    for idx, ch in enumerate(processed_channels, 1):
        v60_str = f"**+{ch['v_60m']:,}**" if ch['v_60m'] > 0 else "0"
        v48_str = f"+{ch['v_48h']:,}" if ch['v_48h'] > 0 else "0"
        md_lines.append(
            f"| #{idx} | **{ch['title']}** | {v60_str} | {v48_str} | {ch['views']:,} | {ch['subs']:,} | {ch['videos_count']:,} |"
        )

    md_lines.append("\n## 2. Chi Tiết Video Từng Kênh (Đã xếp theo View 60m cao nhất)")
    for ch in processed_channels:
        md_lines.append(f"\n<details><summary><b>▶ {ch['title']} (Click để xem {len(ch['videos'])} video)</b></summary>\n")
        md_lines.append("| Tiêu đề Video | View 60 phút | View 48 giờ | Tổng Views |")
        md_lines.append("| :--- | :---: | :---: | :---: |")
        for v in ch["videos"]:
            v60_str = f"**+{v['v_60m']:,}**" if v['v_60m'] > 0 else "0"
            v48_str = f"+{v['v_48h']:,}" if v['v_48h'] > 0 else "0"
            md_lines.append(f"| [{v['title']}](https://youtu.be/{v['id']}) | {v60_str} | {v48_str} | {v['views']:,} |")
        md_lines.append("\n</details>")

    # Ghi file README.md và data.json
    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(history_db, f, ensure_ascii=False)

if __name__ == "__main__":
    main()
