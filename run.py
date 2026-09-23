import os
import json
import time
from datetime import datetime, timezone
from googleapiclient.discovery import build

API_KEY = os.environ.get("YOUTUBE_API_KEY")
CHANNELS_FILE = "channels.txt"
DATA_FILE = "data.json"

youtube = build("youtube", "v3", developerKey=API_KEY)

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"updated_at": None, "channels": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_channel_ids():
    if not os.path.exists(CHANNELS_FILE):
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    clean_ids = []
    for l in lines:
        if "/channel/" in l:
            clean_ids.append(l.split("/channel/")[1].split("/")[0].split("?")[0])
        else:
            clean_ids.append(l)
    return list(dict.fromkeys(clean_ids))

def batch_get_channel_details(channel_ids):
    details = {}
    for i in range(0, len(channel_ids), 50):
        chunk = channel_ids[i:i+50]
        res = youtube.channels().list(
            part="snippet,statistics,contentDetails",
            id=",".join(chunk)
        ).execute()
        for item in res.get("items", []):
            ch_id = item["id"]
            uploads_playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]
            details[ch_id] = {
                "id": ch_id,
                "title": item["snippet"]["title"],
                "subs": int(item["statistics"].get("subscriberCount", 0)),
                "video_count": int(item["statistics"].get("videoCount", 0)),
                "total_views": int(item["statistics"].get("viewCount", 0)),
                "uploads_playlist": uploads_playlist
            }
    return details

def fetch_all_video_ids(uploads_playlist_id):
    video_ids = []
    next_page_token = None
    while True:
        res = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        for item in res.get("items", []):
            v_id = item["contentDetails"].get("videoId")
            if v_id:
                video_ids.append(v_id)
        next_page_token = res.get("nextPageToken")
        if not next_page_token:
            break
    return video_ids

def batch_get_video_stats(video_ids):
    stats = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        res = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(chunk)
        ).execute()
        for item in res.get("items", []):
            vid = item["id"]
            stats[vid] = {
                "id": vid,
                "title": item["snippet"]["title"],
                "views": int(item["statistics"].get("viewCount", 0)),
                "published_at": item["snippet"]["publishedAt"]
            }
    return stats

def main():
    if not API_KEY:
        print("Missing YOUTUBE_API_KEY.")
        return

    channel_ids = get_channel_ids()
    if not channel_ids:
        print("No channels found.")
        return

    db = load_data()
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    now_ts = now_dt.timestamp()

    # Kiểm tra chu kỳ 12h
    last_deep_scan = db.get("last_deep_scan_ts", 0)
    need_deep_scan = (now_ts - last_deep_scan) >= (12 * 3600)

    print(f"[{now_iso}] Starting tracking. Need 12h Deep Scan: {need_deep_scan}")

    channel_meta = batch_get_channel_details(channel_ids)

    for ch_id in channel_ids:
        if ch_id not in channel_meta:
            continue
        meta = channel_meta[ch_id]

        if ch_id not in db["channels"]:
            db["channels"][ch_id] = {
                "id": ch_id,
                "title": meta["title"],
                "subs": meta["subs"],
                "videos_count": meta["video_count"],
                "total_channel_views": meta["total_views"],
                "history": [],
                "tracked_video_ids": [],
                "catalog_snapshots": {},
                "videos": []
            }

        ch_data = db["channels"][ch_id]
        ch_data["title"] = meta["title"]
        ch_data["subs"] = meta["subs"]
        ch_data["videos_count"] = meta["video_count"]
        ch_data["total_channel_views"] = meta["total_views"]

        uploads_pl = meta["uploads_playlist"]

        # --- CHU KỲ 12H (HOẶC CHẠY LẦN ĐẦU TIÊN) ---
        if need_deep_scan or not ch_data.get("tracked_video_ids"):
            print(f"Deep scanning all videos for: {meta['title']}")
            all_video_ids = fetch_all_video_ids(uploads_pl)
            all_stats = batch_get_video_stats(all_video_ids)

            # Pool 1: 50 video mới nhất
            pool1_ids = all_video_ids[:50]
            remaining_ids = all_video_ids[50:]

            catalog = ch_data.get("catalog_snapshots", {})
            has_past_12h_data = bool(catalog)

            candidates = []
            for vid in remaining_ids:
                if vid in all_stats:
                    curr_v = all_stats[vid]["views"]
                    if has_past_12h_data:
                        # Đã có dữ liệu 12h: Tính Delta View tăng thực tế
                        prev_v = catalog.get(vid, curr_v)
                        delta_12h = max(0, curr_v - prev_v)
                    else:
                        # Lần đầu chạy chưa đủ 12h: Delta = 0 để sắp xếp theo Tổng view (curr_v)
                        delta_12h = 0
                    candidates.append((vid, delta_12h, curr_v))

            # Sắp xếp: Ưu tiên Delta 12h cao nhất; nếu bằng nhau sắp theo Tổng view
            candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            pool2_ids = [c[0] for c in candidates[:50]]

            # Cập nhật snapshot chuẩn của toàn bộ video làm mốc so sánh cho 12h sau
            ch_data["catalog_snapshots"] = {vid: s["views"] for vid, s in all_stats.items()}
            ch_data["tracked_video_ids"] = list(dict.fromkeys(pool1_ids + pool2_ids))

        # --- CHU KỲ 15 PHÚT: QUÉT VIEW 100 VIDEO ---
        tracked_ids = ch_data.get("tracked_video_ids", [])
        if not tracked_ids:
            tracked_ids = fetch_all_video_ids(uploads_pl)[:100]
            ch_data["tracked_video_ids"] = tracked_ids

        video_stats = batch_get_video_stats(tracked_ids)

        # Tổng view của 100 video hiệu quả nhất
        active_pool_views = sum(v["views"] for v in video_stats.values())
        ch_data["views"] = active_pool_views

        # Cập nhật danh sách video chi tiết
        video_list = []
        for vid, v in video_stats.items():
            video_list.append({
                "id": vid,
                "title": v["title"],
                "views": v["views"],
                "published_at": v["published_at"]
            })
        video_list.sort(key=lambda x: x["views"], reverse=True)
        ch_data["videos"] = video_list[:50]

        # Lưu lịch sử chuỗi thời gian của tổng 100 video
        history = ch_data.setdefault("history", [])
        history.append([now_iso, active_pool_views])

        if len(history) > 1500:
            ch_data["history"] = history[-1500:]

        # --- TÍNH TOÁN BIẾN ĐỘNG REALTIME ---
        def get_views_ago(minutes_ago):
            target = now_ts - (minutes_ago * 60)
            closest_v = None
            min_diff = float("inf")
            for item in history:
                item_ts = datetime.fromisoformat(item[0]).timestamp()
                diff = abs(item_ts - target)
                if diff < min_diff:
                    min_diff = diff
                    closest_v = item[1]
            return closest_v

        v_15m_ago = get_views_ago(15)
        v_30m_ago = get_views_ago(30)
        v_60m_ago = get_views_ago(60)
        v_24h_ago = get_views_ago(24 * 60)
        v_48h_ago = get_views_ago(48 * 60)

        ch_data["v_15m"] = max(0, active_pool_views - v_15m_ago) if v_15m_ago is not None else 0
        ch_data["v_30m"] = max(0, active_pool_views - v_30m_ago) if v_30m_ago is not None else 0
        ch_data["v_60m"] = max(0, active_pool_views - v_60m_ago) if v_60m_ago is not None else 0
        ch_data["v_24h"] = max(0, active_pool_views - v_24h_ago) if v_24h_ago is not None else 0
        ch_data["v_48h"] = max(0, active_pool_views - v_48h_ago) if v_48h_ago is not None else 0

        avg_15m = ch_data["v_60m"] / 4 if ch_data["v_60m"] > 0 else 0
        ch_data["is_spike"] = ch_data["v_15m"] > max(50, avg_15m * 2)

    if need_deep_scan or last_deep_scan == 0:
        db["last_deep_scan_ts"] = now_ts

    db["updated_at"] = now_iso
    save_data(db)
    print("Cycle completed.")

if __name__ == "__main__":
    main()
