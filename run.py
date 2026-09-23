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
            "title": item["snippet"].get("title", item["id"]),
            "views": int(item["statistics"].get("viewCount", 0)),
            "subs": int(item["statistics"].get("subscriberCount", 0)),
            "videos": int(item["statistics"].get("videoCount", 0)),
            "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"]
        })
    return channels

def get_smart_pool_videos(playlist_id, channel_id, existing_watchlist=[]):
    """
    Smart Pool 100 Video:
    - 70 video mới nhất từ playlist uploads (2 trang 50 + 20)
    - 30 video trong danh sách theo dõi đặc biệt (đang có sóng từ các vòng trước)
    """
    video_ids = []
    
    # 1. Trang 1: 50 video mới nhất
    url1 = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&playlistId={playlist_id}&maxResults=50&key={API_KEY}"
    res1 = api_get(url1)
    for it in res1.get("items", []):
        video_ids.append(it["contentDetails"]["videoId"])
        
    next_page = res1.get("nextPageToken")
    
    # 2. Trang 2: Lấy thêm 20-50 video tiếp theo để đủ 70-100 video
    if next_page:
        url2 = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&playlistId={playlist_id}&maxResults=50&pageToken={next_page}&key={API_KEY}"
        res2 = api_get(url2)
        for it in res2.get("items", []):
            video_ids.append(it["contentDetails"]["videoId"])

    # Gộp thêm danh sách watchlist (video cũ đang cắn view)
    all_target_ids = list(dict.fromkeys(video_ids + existing_watchlist))[:100]

    # Batch 50 IDs mỗi lượt gọi videos.list
    videos = []
    for i in range(0, len(all_target_ids), 50):
        chunk = all_target_ids[i:i+50]
        v_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={','.join(chunk)}&key={API_KEY}"
        v_res = api_get(v_url)
        for item in v_res.get("items", []):
            pub_at = item["snippet"].get("publishedAt", "")
            videos.append({
                "id": item["id"],
                "title": item["snippet"].get("title", "Video không tiêu đề"),
                "published_at": pub_at,
                "views": int(item["statistics"].get("viewCount", 0)),
                "likes": int(item["statistics"].get("likeCount", 0)),
                "comments": int(item["statistics"].get("commentCount", 0))
            })
    return videos

def calculate_video_deltas(history, current_views):
    """Tính 30p, 60p, 24h, 48h và chuỗi 48 vạch sóng"""
    now = datetime.now(timezone.utc)
    if not history:
        return 0, 0, 0, 0, [0] * 48

    def get_delta(minutes_ago):
        t_target = now - timedelta(minutes=minutes_ago)
        rec = min(history, key=lambda x: abs(parse_time(x["t"]) - t_target), default=None)
        # Chỉ trừ nếu bản ghi đó cách ít nhất 10 phút
        if rec and (parse_time(rec["t"]) <= now - timedelta(minutes=10)):
            return max(0, current_views - rec["v"])
        return 0

    v_30m = get_delta(30)
    v_60m = get_delta(60)
    v_24h = get_delta(24 * 60)
    v_48h = get_delta(48 * 60)

    # 48 cột từng giờ
    hourly_views = [0] * 48
    for i in range(48):
        t_end = now - timedelta(hours=47 - i)
        t_start = t_end - timedelta(hours=1)
        r_start = min(history, key=lambda x: abs(parse_time(x["t"]) - t_start), default=None)
        r_end = min(history, key=lambda x: abs(parse_time(x["t"]) - t_end), default=None)
        if r_start and r_end and (parse_time(r_end["t"]) > parse_time(r_start["t"])):
            hourly_views[i] = max(0, r_end["v"] - r_start["v"])

    return v_30m, v_60m, v_24h, v_48h, hourly_views

def make_sparkline(hourly_views):
    bars = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    max_h = max(hourly_views) if max(hourly_views) > 0 else 1
    return "".join(bars[min(8, int((val / max_h) * 8))] if val > 0 else " " for val in hourly_views)

def calculate_channel_longterm(daily_history, current_total_views):
    """Tính tăng trưởng dài hạn của kênh: 1 ngày, 7 ngày, 30 ngày, 90 ngày"""
    now = datetime.now(timezone.utc)
    
    def get_diff(days_ago):
        target_date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        if target_date in daily_history:
            return max(0, current_total_views - daily_history[target_date])
        # Nếu chưa đủ ngày thì lấy ngày cũ nhất có thể
        sorted_dates = sorted(daily_history.keys())
        if sorted_dates:
            oldest_date = sorted_dates[0]
            if (now - datetime.strptime(oldest_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days >= days_ago:
                return max(0, current_total_views - daily_history[oldest_date])
        return 0

    return {
        "d_1": get_diff(1),
        "d_7": get_diff(7),
        "d_30": get_diff(30),
        "d_90": get_diff(90)
    }

def prune_history(history, hours=50):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [e for e in history if parse_time(e["t"]) >= cutoff]

def main():
    if not API_KEY or not os.path.exists("channels.txt"):
        return

    with open("channels.txt", "r", encoding="utf-8") as f:
        channel_ids = [line.strip() for line in f if line.strip()]

    # Khởi tạo dữ liệu
    db = {
        "history": {"videos": {}},
        "daily_channel_history": {},
        "watchlist": {}
    }
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    db["history"] = loaded.get("history", {"videos": {}})
                    db["daily_channel_history"] = loaded.get("daily_channel_history", {})
                    db["watchlist"] = loaded.get("watchlist", {})
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_display = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    today_key = now.strftime("%Y-%m-%d")

    channels_data = get_channel_data(channel_ids)
    channels_summary = {}

    for ch in channels_data:
        ch_id = ch["id"]

        # 1. BỀN VỮNG: Lưu chốt tổng view kênh theo ngày
        ch_daily = db["daily_channel_history"].setdefault(ch_id, {})
        ch_daily[today_key] = ch["views"]
        longterm_stats = calculate_channel_longterm(ch_daily, ch["views"])

        # 2. REALTIME: Quét Smart Pool 100 video
        existing_ch_watchlist = db["watchlist"].get(ch_id, [])
        videos = get_smart_pool_videos(ch["uploads_playlist"], ch_id, existing_ch_watchlist)

        video_list = []
        ch_total_v30m = 0
        ch_total_v60m = 0
        ch_total_v24h = 0
        ch_total_v48h = 0
        ch_hourly_sum = [0] * 48
        new_active_videos = []

        for vid in videos:
            v_id = vid["id"]
            vid_hist = db["history"]["videos"].setdefault(v_id, [])

            v_30m, v_60m, v_24h, v_48h, v_hourly = calculate_video_deltas(vid_hist, vid["views"])

            # Lưu mốc & dọn dẹp sau 48h
            vid_hist.append({"t": now_iso, "v": vid["views"]})
            db["history"]["videos"][v_id] = prune_history(vid_hist, hours=50)

            # Cộng dồn Realtime Kênh
            ch_total_v30m += v_30m
            ch_total_v60m += v_60m
            ch_total_v24h += v_24h
            ch_total_v48h += v_48h
            for i in range(48):
                ch_hourly_sum[i] += v_hourly[i]

            # Nếu video có view tăng, giữ vào Watchlist
            if v_30m > 5 or v_60m > 10:
                new_active_videos.append(v_id)

            # Tính tuổi thọ và vận tốc view
            pub_date = parse_time(vid["published_at"]) if vid["published_at"] else now
            hours_old = max(1, int((now - pub_date).total_seconds() / 3600))
            days_old = max(1, int(hours_old / 24))
            views_per_day = int(vid["views"] / days_old)

            v_spark = make_sparkline(v_hourly)
            is_spike = v_30m > 10 and (v_30m > (v_24h / 48) * 2)

            video_list.append({
                "id": v_id,
                "title": vid["title"],
                "views": vid["views"],
                "likes": vid["likes"],
                "comments": vid["comments"],
                "published_at": vid["published_at"],
                "days_old": days_old,
                "views_per_day": views_per_day,
                "v_30m": v_30m,
                "v_60m": v_60m,
                "v_24h": v_24h,
                "v_48h": v_48h,
                "sparkline": v_spark,
                "is_spike": is_spike
            })

        # Cập nhật watchlist video đang cắn sóng (tối đa 30 video)
        db["watchlist"][ch_id] = list(dict.fromkeys(new_active_videos))[:30]

        # Sắp xếp video: Ưu tiên view 30p, sau đó 60p, 48h
        video_list.sort(key=lambda x: (x["v_30m"], x["v_60m"], x["v_48h"], x["views_per_day"]), reverse=True)

        avg_views = int(ch["views"] / ch["videos"]) if ch["videos"] > 0 else 0
        ch_spark = make_sparkline(ch_hourly_sum)
        ch_spike = ch_total_v30m > 25

        channels_summary[ch_id] = {
            "id": ch_id,
            "title": ch["title"],
            "views": ch["views"],
            "subs": ch["subs"],
            "videos_count": ch["videos"],
            "avg_views": avg_views,
            "v_30m": ch_total_v30m,
            "v_60m": ch_total_v60m,
            "v_24h": ch_total_v24h,
            "v_48h": ch_total_v48h,
            "longterm": longterm_stats,
            "sparkline": ch_spark,
            "is_spike": ch_spike,
            "videos": video_list
        }

    output_data = {
        "updated_at": now_iso,
        "updated_at_display": now_display,
        "channels": channels_summary,
        "history": db["history"],
        "daily_channel_history": db["daily_channel_history"],
        "watchlist": db["watchlist"]
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False)

    # Cập nhật README.md
    md = [
        f"# 📊 YouTube Realtime Analytics Hub (Smart Pool 100 Engine)",
        f"*Cập nhật: `{now_display}`*\n",
        "| Top | Kênh | View 30p | View 60p | 48 Giờ | 48 Cột Giờ | 7 Ngày Qua | Tổng Toàn Thời Gian | Subs |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    ]
    sorted_ch = sorted(channels_summary.values(), key=lambda x: (x["v_30m"], x["v_60m"], x["v_48h"], x["views"]), reverse=True)
    for idx, ch in enumerate(sorted_ch, 1):
        v30 = f"+{ch['v_30m']:,}" if ch['v_30m'] > 0 else "0"
        v60 = f"+{ch['v_60m']:,}" if ch['v_60m'] > 0 else "0"
        v48 = f"+{ch['v_48h']:,}" if ch['v_48h'] > 0 else "0"
        d7 = f"+{ch['longterm']['d_7']:,}" if ch['longterm']['d_7'] > 0 else "-"
        md.append(f"| #{idx} | **{ch['title']}** | `{v30}` | `{v60}` | **{v48}** | `{ch['sparkline']}` | {d7} | {ch['views']:,} | {ch['subs']:,} |")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

if __name__ == "__main__":
    main()
