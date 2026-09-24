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
        except Exception as e:
            print(f"Error loading {DATA_FILE}: {e}")
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

def get_latest_100_video_ids(uploads_playlist_id):
    video_ids = []
    next_page_token = None
    for _ in range(2):  # 2 trang x 50 = 100 video mới nhất
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

def fetch_all_channel_video_ids(uploads_playlist_id):
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

    # Mốc YouTube Ngày Mới: 00:00 UTC (07:00 AM VN)
    today_str_utc = now_dt.strftime("%Y-%m-%d")
    last_full_scan_date = db.get("last_full_scan_date", "")
    
    # Kích hoạt quét toàn kênh khi sang ngày UTC mới và sau 00:00 UTC
    need_full_scan = (last_full_scan_date != today_str_utc)

    print(f"[{now_iso}] Quét định kỳ. Full Scan ngày {today_str_utc}: {need_full_scan}")
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
                "history_channel": [],
                "history_recent_pool": [],
                "video_histories": {},
                "catalog_snapshots": {},
                "revived_videos": [],
                "videos": [],
                "slices_15m": [0, 0, 0, 0]
            }

        ch_data = db["channels"][ch_id]
        ch_data["title"] = meta["title"]
        ch_data["subs"] = meta["subs"]
        ch_data["videos_count"] = meta["video_count"]
        channel_real_total = meta["total_views"]
        ch_data["total_channel_views"] = channel_real_total

        uploads_pl = meta["uploads_playlist"]

        # 1. QUÉT 100 VIDEO MỚI NHẤT (DÙNG CHO 15P, 60P)
        latest_100_ids = get_latest_100_video_ids(uploads_pl)
        recent_stats = batch_get_video_stats(latest_100_ids)
        recent_total_views = sum(v["views"] for v in recent_stats.values())

        h_recent = ch_data.setdefault("history_recent_pool", [])
        h_recent.append([now_iso, recent_total_views])
        if len(h_recent) > 1500:
            ch_data["history_recent_pool"] = h_recent[-1500:]

        v_hists = ch_data.setdefault("video_histories", {})
        video_list = []
        for vid, v in recent_stats.items():
            curr_v = v["views"]
            vh = v_hists.setdefault(vid, [])
            vh.append([now_ts, curr_v])
            if len(vh) > 200:
                vh = vh[-200:]
                v_hists[vid] = vh

            def get_vid_old(target_sec):
                t = now_ts - target_sec
                c = None
                m_diff = float("inf")
                for s_ts, s_v in vh:
                    d = abs(s_ts - t)
                    if d < m_diff:
                        m_diff = d
                        c = s_v
                return c

            v_60m_old = get_vid_old(3600)
            v_48h_old = get_vid_old(48 * 3600)

            video_list.append({
                "id": vid,
                "title": v["title"],
                "views": curr_v,
                "v_60m": max(0, curr_v - v_60m_old) if v_60m_old is not None else 0,
                "v_48h": max(0, curr_v - v_48h_old) if v_48h_old is not None else 0,
                "published_at": v["published_at"]
            })
        ch_data["videos"] = video_list

        def get_recent_views_ago(minutes_ago):
            target = now_ts - (minutes_ago * 60)
            c_val = None
            m_diff = float("inf")
            for item in h_recent:
                item_ts = datetime.fromisoformat(item[0]).timestamp()
                diff = abs(item_ts - target)
                if diff < m_diff:
                    m_diff = diff
                    c_val = item[1]
            return c_val

        rv_0 = recent_total_views
        rv_15 = get_recent_views_ago(15) or rv_0
        rv_30 = get_recent_views_ago(30) or rv_15
        rv_45 = get_recent_views_ago(45) or rv_30
        rv_60 = get_recent_views_ago(60) or rv_45
        rv_24h = get_recent_views_ago(24 * 60)
        rv_48h = get_recent_views_ago(48 * 60)

        c4 = max(0, rv_0 - rv_15)
        c3 = max(0, rv_15 - rv_30)
        c2 = max(0, rv_30 - rv_45)
        c1 = max(0, rv_45 - rv_60)
        total_60 = max(0, rv_0 - rv_60)

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
        ch_data["v_30m"] = max(0, rv_0 - rv_30)
        ch_data["v_60m"] = total_60
        ch_data["slices_15m"] = [c1, c2, c3, c4]

        v_24h_recent = max(0, rv_0 - rv_24h) if rv_24h is not None else 0
        v_48h_recent = max(0, rv_0 - rv_48h) if rv_48h is not None else 0
        ch_data["v_24h_recent"] = v_24h_recent
        ch_data["v_48h_recent"] = v_48h_recent

        # 2. CHỈ SỐ TOÀN KÊNH (24H, 48H, 7N, 30N, 90N) TỪ TOTAL_CHANNEL_VIEWS
        h_channel = ch_data.setdefault("history_channel", [])
        h_channel.append([now_iso, channel_real_total])
        if len(h_channel) > 3000:
            ch_data["history_channel"] = h_channel[-3000:]

        def get_channel_views_ago(hours_ago):
            target = now_ts - (hours_ago * 3600)
            c_val = None
            m_diff = float("inf")
            for item in h_channel:
                item_ts = datetime.fromisoformat(item[0]).timestamp()
                diff = abs(item_ts - target)
                if diff < m_diff:
                    m_diff = diff
                    c_val = item[1]
            return c_val

        ch_24h = get_channel_views_ago(24)
        ch_48h = get_channel_views_ago(48)
        ch_7d = get_channel_views_ago(7 * 24)
        ch_30d = get_channel_views_ago(30 * 24)
        ch_90d = get_channel_views_ago(90 * 24)

        ch_data["v_24h"] = max(0, channel_real_total - ch_24h) if ch_24h is not None else 0
        ch_data["v_48h"] = max(0, channel_real_total - ch_48h) if ch_48h is not None else 0
        ch_data["v_7d"] = max(0, channel_real_total - ch_7d) if ch_7d is not None else None
        ch_data["v_30d"] = max(0, channel_real_total - ch_30d) if ch_30d is not None else None
        ch_data["v_90d"] = max(0, channel_real_total - ch_90d) if ch_90d is not None else None

        # Độ lệch giữa toàn kênh và 100 video mới
        diff_24h = max(0, ch_data["v_24h"] - ch_data["v_24h_recent"])
        ch_data["anomaly_diff_24h"] = diff_24h
        ch_data["has_revived_anomaly"] = diff_24h >= 500

        # 3. FULL CATALOG SCAN: QUÉT TẤT CẢ VIDEO ĐỂ TÌM VIDEO CŨ NỔ VIEW (00:00 UTC)
        if need_full_scan or not ch_data.get("catalog_snapshots"):
            print(f"Quét toàn kênh tìm video cổ cắn đề xuất: {meta['title']}")
            all_video_ids = fetch_all_channel_video_ids(uploads_pl)
            all_stats = batch_get_video_stats(all_video_ids)

            catalog = ch_data.get("catalog_snapshots", {})
            revived = []

            if catalog:
                latest_set = set(latest_100_ids)
                for vid, s in all_stats.items():
                    if vid not in latest_set and vid in catalog:
                        v_delta = max(0, s["views"] - catalog[vid])
                        if v_delta >= 100:
                            revived.append({
                                "id": vid,
                                "title": s["title"],
                                "views": s["views"],
                                "v_delta_24h": v_delta,
                                "published_at": s["published_at"]
                            })

                revived.sort(key=lambda x: x["v_delta_24h"], reverse=True)
                ch_data["revived_videos"] = revived[:20]

            ch_data["catalog_snapshots"] = {vid: s["views"] for vid, s in all_stats.items()}

        avg_15m = ch_data["v_60m"] / 4 if ch_data["v_60m"] > 0 else 0
        ch_data["is_spike"] = ch_data["v_15m"] > max(50, avg_15m * 2)

    if need_full_scan:
        db["last_full_scan_date"] = today_str_utc

    db["updated_at"] = now_iso
    save_data(db)
    print("Hoàn tất chu kỳ quét.")

if __name__ == "__main__":
    main()
