import os
import json
import urllib.request
from datetime import datetime

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

def main():
    if not API_KEY:
        print("Lỗi: Chưa thiết lập YOUTUBE_API_KEY")
        return

    if not os.path.exists("channels.txt"):
        print("Lỗi: Không tìm thấy file channels.txt")
        return

    with open("channels.txt", "r", encoding="utf-8") as f:
        channel_ids = [line.strip() for line in f if line.strip()]

    channels = get_channel_data(channel_ids)

    old_data = {}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
        except Exception:
            pass

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    new_snapshot = {"timestamp": now_str, "channels": {}}

    md_lines = [
        "# 📊 Báo Cáo View YouTube Tự Động",
        f"*Lần cập nhật gần nhất: `{now_str}`*\n",
        "## 1. Bảng Xếp Hạng 20 Kênh",
        "| Tên Kênh | Tổng Views | Tăng Trưởng | Subs | Số Video |",
        "| :--- | :---: | :---: | :---: | :---: |"
    ]

    for ch in channels:
        ch_id = ch["id"]
        old_views = old_data.get("channels", {}).get(ch_id, {}).get("views", ch["views"])
        delta = ch["views"] - old_views
        delta_str = f"+{delta:,}" if delta > 0 else (f"{delta:,}" if delta < 0 else "0")

        md_lines.append(f"| **{ch['title']}** | {ch['views']:,} | `{delta_str}` | {ch['subs']:,} | {ch['videos']:,} |")

        videos = get_latest_videos(ch["uploads_playlist"], max_results=50)
        new_snapshot["channels"][ch_id] = {
            "title": ch["title"],
            "views": ch["views"],
            "subs": ch["subs"],
            "videos": {v["id"]: {"title": v["title"], "views": v["views"]} for v in videos}
        }

    md_lines.append("\n## 2. Chi Tiết Video (Bấm vào từng kênh để xem)")
    for ch_id, ch_info in new_snapshot["channels"].items():
        md_lines.append(f"\n<details><summary><b>▶ {ch_info['title']} ({len(ch_info['videos'])} video mới nhất)</b></summary>\n")
        md_lines.append("| Tiêu đề Video | Lượt Views |")
        md_lines.append("| :--- | :---: |")
        for v_id, v_data in ch_info["videos"].items():
            md_lines.append(f"| [{v_data['title']}](https://youtu.be/{v_id}) | {v_data['views']:,} |")
        md_lines.append("\n</details>")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(new_snapshot, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
