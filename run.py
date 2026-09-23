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

def get_latest_50_video_ids(uploads_playlist_id):
    res = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_playlist_id,
        maxResults=50
    ).execute()
    return [item["contentDetails"]["videoId"] for item in res.get("items", []) if item.get("contentDetails", {}).get("videoId")]

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
            v_id = item.get("contentDetails", {}).get("videoId")
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

    last_deep_scan = db.get("last_deep_scan_ts", 0)
    need_deep_scan = (now_ts - last_deep_scan) >= (12 * 3600)

    print(f"[{now_iso}] Tracking run. Deep scan required: {need_deep_scan}")

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
                "video_histories": {},
                "videos": [],
                "slices_15m": [0, 0, 0, 0]
            }

        ch_data = db["channels"][ch_id]
        ch_data["title"] = meta["title"]
        ch_data["subs"] = meta["subs"]
        ch_data["videos_count"] = meta["video_count"]
        ch_data["total_channel_views"] = meta["total_views"]

        uploads_pl = meta["uploads_playlist"]
        latest_50 = get_latest_50_video_ids(uploads_pl)

        if need_deep_scan or not ch_data.get("tracked_video_ids"):
            print(f"Deep scanning: {meta['title']}")
            all_video_ids = fetch_all_video_ids(uploads_pl)
            all_stats = batch_get_video_stats(all_video_ids)

            pool1_ids = latest_50
            remaining_ids = [v for v in all_video_ids if v not in pool1_ids]

            catalog = ch_data.get("catalog_snapshots", {})
            has_past = bool(catalog)

            candidates = []
            for vid in remaining_ids:
                if vid in all_stats:
                    curr_v = all_stats[vid]["views"]
                    delta_12h = max(0, curr_v - catalog.get(vid, curr_v)) if has_past else 0
                    candidates.append((vid, delta_12h, curr_v))

            candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            pool2_ids = [c[0] for c in candidates[:50]]

            ch_data["catalog_snapshots"] = {vid: s["views"] for vid, s in all_stats.items()}
            ch_data["tracked_video_ids"] = list(dict.fromkeys(pool1_ids + pool2_ids))
        else:
            existing_tracked = ch_data.get("tracked_video_ids", [])
            merged = list(dict.fromkeys(latest_50 + existing_tracked))[:100]
            ch_data["tracked_video_ids"] = merged

        tracked_ids = ch_data.get("tracked_video_ids", [])
        video_stats = batch_get_video_stats(tracked_ids)
        active_pool_views = sum(v["views"] for v in video_stats.values())
        ch_data["views"] = active_pool_views

        v_hists = ch_data.setdefault("video_histories", {})
        video_list = []

        for vid, v in video_stats.items():
            curr_v = v["views"]
            vh = v_hists.setdefault(vid, [])
            vh.append([now_ts, curr_v])
            if len(vh) > 200:
                vh = vh[-200:]
                v_hists[vid] = vh

            def get_vid_view_ago(target_seconds):
                target = now_ts - target_seconds
                closest = None
                min_diff = float("inf")
                for snap_ts, snap_v in vh:
                    d = abs(snap_ts - target)
                    if d < min_diff:
                        min_diff = d
                        closest = snap_v
                return closest

            v_60m_old = get_vid_view_ago(3600)
            v_48h_old = get_vid_view_ago(48 * 3600)

            v_delta_60m = max(0, curr_v - v_60m_old) if v_60m_old is not None else 0
            v_delta_48h = max(0, curr_v - v_48h_old) if v_48h_old is not None else 0

            video_list.append({
                "id": vid,
                "title": v["title"],
                "views": curr_v,
                "v_60m": v_delta_60m,
                "v_48h": v_delta_48h,
                "published_at": v["published_at"]
            })

        ch_data["videos"] = video_list

        history = ch_data.setdefault("history", [])
        history.append([now_iso, active_pool_views])
        if len(history) > 1500:
            ch_data["history"] = history[-1500:]

        def get_channel_views_ago(minutes_ago):
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

        v_0m = active_pool_views
        v_15m_ago = get_channel_views_ago(15)
        v_30m_ago = get_channel_views_ago(30)
        v_45m_ago = get_channel_views_ago(45)
        v_60m_ago = get_channel_views_ago(60)
        v_24h_ago = get_channel_views_ago(24 * 60)
        v_48h_ago = get_channel_views_ago(48 * 60)

        # Tính toán chuẩn xác 4 cột 15 phút ngay tại Backend
        m15 = v_15m_ago if v_15m_ago is not None else v_0m
        m30 = v_30m_ago if v_30m_ago is not None else m15
        m45 = v_45m_ago if v_45m_ago is not None else m30
        m60 = v_60m_ago if v_60m_ago is not None else m45

        c4 = max(0, v_0m - m15)
        c3 = max(0, m15 - m30)
        c2 = max(0, m30 - m45)
        c1 = max(0, m45 - m60)

        total_60 = max(0, v_0m - m60)

        # Khóa chuẩn hóa: Tổng 4 cột luôn bằng v_60m tuyệt đối
        sum_c = c1 + c2 + c3 + c4
        if sum_c > 0 and sum_c != total_60:
            ratio = total_60 / sum_c
            c1 = round(c1 * ratio)
            c2 = round(c2 * ratio)
            c3 = round(c3 * ratio)
            c4 = max(0, total_60 - (c1 + c2 + c3))
        elif total_60 == 0:
            c1, c2, c3, c4 = 0, 0, 0, 0

        ch_data["v_15m"] = c4
        ch_data["v_30m"] = max(0, v_0m - m30)
        ch_data["v_60m"] = total_60
        ch_data["v_24h"] = max(0, v_0m - v_24h_ago) if v_24h_ago is not None else 0
        ch_data["v_48h"] = max(0, v_0m - v_48h_ago) if v_48h_ago is not None else 0
        ch_data["slices_15m"] = [c1, c2, c3, c4]

        avg_15m = ch_data["v_60m"] / 4 if ch_data["v_60m"] > 0 else 0
        ch_data["is_spike"] = ch_data["v_15m"] > max(50, avg_15m * 2)

    if need_deep_scan or last_deep_scan == 0:
        db["last_deep_scan_ts"] = now_ts

    db["updated_at"] = now_iso
    save_data(db)
    print("Cycle complete.")

if __name__ == "__main__":
    main()
