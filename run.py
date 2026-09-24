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
        ch_id = l
        if "/channel/" in l:
            ch_id = l.split("/channel/")[1].split("/")[0].split("?")[0]
            
        # Lọc chặt ID chuẩn
        if ch_id.startswith("UC") and len(ch_id) == 24:
            clean_ids.append(ch_id)
        else:
            print(f"Bỏ qua ID không hợp lệ: {l}")
            
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
    max_pages = 25  # Giới hạn lấy 1250 video để CHỐNG SẬP QUOTA API
    pages_fetched = 0
    
    while pages_fetched < max_pages:
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
        pages_fetched += 1
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

    # Chu kỳ Full Scan lúc 00:00 UTC (07:00 AM VN)
    today_str_utc = now_dt.strftime("%Y-%m-%d")
    last_full_scan_date = db.get("last_full_scan_date", "")
    need_full_scan = (last_full_scan_date != today_str_utc)

    print(f"[{now_iso}] Quét định kỳ. Cần Full Scan ngày {today_str_utc}: {need_full_scan}")
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
                "history_full_catalog": [], 
                "video_histories": {},
                "catalog_snapshots": {},    
                "revived_videos": [],       
                "videos": [],
                "slices_15m": [0, 0, 0, 0],
                "slices_48h": [0] * 48
            }

        ch_data = db["channels"][ch_id]
        ch_data["title"] = meta["title"]
        ch_data["subs"] = meta["subs"]
        ch_data["videos_count"] = meta["video_count"]
        ch_data["total_channel_views"] = meta["total_views"]
        uploads_pl = meta["uploads_playlist"]

        # ====================================================================
        # LUỒNG 1: QUÉT 100 VIDEO MỚI NHẤT (SỬA LỖI SLIDING WINDOW DROP)
        # ====================================================================
        latest_100_ids = get_latest_100_video_ids(uploads_pl)
        recent_stats = batch_get_video_stats(latest_100_ids)

        v_hists = ch_data.setdefault("video_histories", {})
        
        # DỌN DẸP JSON: Xóa lịch sử video đã rớt khỏi Top 100 để giảm dung lượng file
        current_100_set = set(latest_100_ids)
        keys_to_remove = [k for k in v_hists.keys() if k not in current_100_set]
        for k in keys_to_remove:
            del v_hists[k]

        video_list = []
        c1 = c2 = c3 = c4 = 0
        v_24h_recent = 0
        v_48h_recent = 0
        slices_48h = [0] * 48

        # Tính tăng trưởng dựa trên TỪNG VIDEO để tránh bị âm (về 0) khi có video rớt rổ
        for vid, v in recent_stats.items():
            curr_v = v["views"]
            vh = v_hists.setdefault(vid, [])
            vh.append([now_ts, curr_v])
            
            # Giữ tối đa 200 mốc cho mỗi video (đủ 48 giờ để vẽ cột)
            if len(vh) > 200:
                vh = vh[-200:]
                v_hists[vid] = vh

            def get_single_vid_old(minutes_ago):
                target = now_ts - (minutes_ago * 60)
                closest_val = None
                m_diff = float("inf")
                target_ts = None
                for s_ts, s_v in vh:
                    diff = abs(s_ts - target)
                    if diff < m_diff:
                        m_diff = diff
                        closest_val = s_v
                        target_ts = s_ts
                
                # Bắt buộc mốc tìm được phải cách hiện tại một khoảng tương đối
                if target_ts and (now_ts - target_ts) >= (minutes_ago * 0.4 * 60):
                    return closest_val
                # Nếu video còn quá mới chưa đủ lịch sử, lấy mốc cũ nhất có thể
                if len(vh) > 1:
                    return vh[0][1]
                return curr_v # Nếu hoàn toàn mới, delta sẽ = 0

            v_15 = get_single_vid_old(15)
            v_30 = get_single_vid_old(30)
            v_45 = get_single_vid_old(45)
            v_60 = get_single_vid_old(60)
            v_24h_old = get_single_vid_old(24 * 60)
            v_48h_old = get_single_vid_old(48 * 60)

            if v_15 is not None: c4 += max(0, curr_v - v_15)
            if v_30 is not None and v_15 is not None: c3 += max(0, v_15 - v_30)
            if v_45 is not None and v_30 is not None: c2 += max(0, v_30 - v_45)
            if v_60 is not None and v_45 is not None: c1 += max(0, v_45 - v_60)
            
            if v_24h_old is not None: v_24h_recent += max(0, curr_v - v_24h_old)
            if v_48h_old is not None: v_48h_recent += max(0, curr_v - v_48h_old)

            for h in range(48, 0, -1):
                v_start = get_single_vid_old(h * 60)
                v_end = get_single_vid_old((h - 1) * 60)
                if v_start is not None and v_end is not None:
                    slices_48h[48 - h] += max(0, v_end - v_start)

            video_list.append({
                "id": vid,
                "title": v["title"],
                "views": curr_v,
                "v_60m": max(0, curr_v - (v_60 if v_60 is not None else curr_v)),
                "v_48h": max(0, curr_v - (v_48h_old if v_48h_old is not None else curr_v)),
                "published_at": v["published_at"]
            })
        
        ch_data["videos"] = video_list
        ch_data["v_15m"] = c4
        ch_data["v_30m"] = c3 + c4
        ch_data["v_60m"] = c1 + c2 + c3 + c4
        ch_data["slices_15m"] = [c1, c2, c3, c4]
        ch_data["slices_48h"] = slices_48h
        ch_data["v_24h_recent"] = v_24h_recent
        ch_data["v_48h_recent"] = v_48h_recent

        # ====================================================================
        # LUỒNG 2: QUÉT CATALOG ĐỊNH KỲ 24H
        # ====================================================================
        h_catalog = ch_data.setdefault("history_full_catalog", [])

        if need_full_scan or not ch_data.get("catalog_snapshots"):
            print(f"-> Full Catalog Scan toàn bộ video: {meta['title']}")
            all_video_ids = fetch_all_channel_video_ids(uploads_pl)
            all_stats = batch_get_video_stats(all_video_ids)
            catalog_total_views = sum(s["views"] for s in all_stats.values())

            h_catalog.append([now_iso, catalog_total_views])
            if len(h_catalog) > 300:
                ch_data["history_full_catalog"] = h_catalog[-300:]

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

        def get_catalog_views_ago(hours_ago):
            target = now_ts - (hours_ago * 3600)
            c_val = None
            m_diff = float("inf")
            target_ts_found = None
            for item in h_catalog:
                item_ts = datetime.fromisoformat(item[0]).timestamp()
                diff = abs(item_ts - target)
                if diff < m_diff:
                    m_diff = diff
                    c_val = item[1]
                    target_ts_found = item_ts
            if target_ts_found and (now_ts - target_ts_found) >= (hours_ago * 0.6 * 3600):
                return c_val
            return None

        cat_now = h_catalog[-1][1] if h_catalog else None
        cat_24h = get_catalog_views_ago(24)
        cat_48h = get_catalog_views_ago(48)

        if cat_now is not None and cat_24h is not None and cat_now >= cat_24h:
            ch_data["v_24h"] = cat_now - cat_24h
        else:
            ch_data["v_24h"] = v_24h_recent

        if cat_now is not None and cat_48h is not None and cat_now >= cat_48h:
            ch_data["v_48h"] = cat_now - cat_48h
        else:
            ch_data["v_48h"] = v_48h_recent

        diff_24h = max(0, ch_data["v_24h"] - ch_data["v_24h_recent"])
        ch_data["anomaly_diff_24h"] = diff_24h
        ch_data["has_revived_anomaly"] = (diff_24h >= 500) or (len(ch_data.get("revived_videos", [])) > 0)

        # ====================================================================
        # LUỒNG 3: VIEW DÀI HẠN (7N, 30N, 90N) TỪ TỔNG VIEW TOÀN KÊNH
        # ====================================================================
        channel_real_total = meta["total_views"]
        h_channel = ch_data.setdefault("history_channel", [])
        h_channel.append([now_iso, channel_real_total])
        if len(h_channel) > 3000:
            ch_data["history_channel"] = h_channel[-3000:]

        def get_channel_views_ago(days_ago):
            target = now_ts - (days_ago * 86400)
            c_val = None
            m_diff = float("inf")
            target_ts_found = None
            for item in h_channel:
                item_ts = datetime.fromisoformat(item[0]).timestamp()
                diff = abs(item_ts - target)
                if diff < m_diff:
                    m_diff = diff
                    c_val = item[1]
                    target_ts_found = item_ts
            if target_ts_found and (now_ts - target_ts_found) >= (days_ago * 0.7 * 86400):
                return c_val
            return None

        ch_7d = get_channel_views_ago(7)
        ch_30d = get_channel_views_ago(30)
        ch_90d = get_channel_views_ago(90)

        ch_data["v_7d"] = max(0, channel_real_total - ch_7d) if ch_7d is not None else None
        ch_data["v_30d"] = max(0, channel_real_total - ch_30d) if ch_30d is not None else None
        ch_data["v_90d"] = max(0, channel_real_total - ch_90d) if ch_90d is not None else None

        avg_15m = ch_data["v_60m"] / 4 if ch_data["v_60m"] > 0 else 0
        ch_data["is_spike"] = ch_data["v_15m"] > max(50, avg_15m * 2)

        # Tính sparkline động từ 12 cột cuối của slices_48h (biểu diễn 12 giờ qua)
        chars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
        pts = slices_48h[-12:] if len(slices_48h) >= 12 else slices_48h
        max_d = max(pts) if pts and max(pts) > 0 else 1
        ch_data["sparkline"] = "".join(chars[min(7, int((d / max_d) * 7))] for d in pts)

    if need_full_scan:
        db["last_full_scan_date"] = today_str_utc

    db["updated_at"] = now_iso
    save_data(db)
    print("Hoàn tất chu kỳ cập nhật dữ liệu 3 luồng.")

if __name__ == "__main__":
    main()
