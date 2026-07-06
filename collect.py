"""
collect.py — yt-dlp로 유튜브 채널 데이터 수집 (API 키 불필요)

수집 결과
  - data/videos.csv    : 영상별 메타데이터(제목, 조회수, 길이, 업로드일, 좋아요 등)
  - data/comments.csv  : (선택) 상위 영상들의 댓글 텍스트

사용 예시
  python collect.py "https://www.youtube.com/@채널핸들/videos"
  python collect.py "@채널핸들" --max-videos 200
  python collect.py "@채널핸들" --with-comments --comment-videos 20 --max-comments 200

주의
  - yt-dlp가 주는 view_count는 '수집 시점의 누적 조회수' 스냅샷입니다.
    특정 영상의 시간별 조회수 곡선은 유튜브가 공개하지 않으므로,
    조회수 '시계열'은 업로드일 기준 집계(월별/누적)로 분석합니다.
"""
import argparse
import csv
import os
import sys
import time

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def normalize_channel_url(target: str) -> str:
    """@handle, 채널ID, 전체 URL 무엇이 와도 /videos 형태로 정규화."""
    target = target.strip()
    if target.startswith("http"):
        # 이미 URL이면 videos 탭이 아니어도 그대로 둠 (yt-dlp가 처리)
        return target
    if target.startswith("@"):
        return f"https://www.youtube.com/{target}/videos"
    if target.startswith("UC") and len(target) == 24:
        return f"https://www.youtube.com/channel/{target}/videos"
    # 그 외는 핸들로 간주
    return f"https://www.youtube.com/@{target}/videos"


def _collect_flat_entries(entry, out):
    if not entry:
        return
    if entry.get("_type") == "playlist" and entry.get("entries"):
        for sub in entry["entries"]:
            _collect_flat_entries(sub, out)
        return
    video_id = entry.get("id")
    if not video_id:
        return
    url = entry.get("webpage_url") or entry.get("url")
    if not url or not str(url).startswith("http"):
        url = f"https://www.youtube.com/watch?v={video_id}"
    out.append({
        "id": video_id,
        "title": entry.get("title"),
        "upload_date": entry.get("upload_date"),
        "duration": entry.get("duration"),
        "view_count": entry.get("view_count"),
        "like_count": entry.get("like_count"),
        "comment_count": entry.get("comment_count"),
        "channel": entry.get("channel"),
        "channel_id": entry.get("channel_id"),
        "categories": entry.get("categories"),
        "tags": entry.get("tags"),
        "webpage_url": url,
    })


def list_video_entries(channel_url: str, max_videos: int):
    """flat 추출로 영상 기본 정보 목록을 빠르게 가져온다."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp가 필요합니다: pip install -r requirements.txt")
    opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        "playlistend": max_videos if max_videos else None,
    }
    entries_out = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries") or []
        for e in entries:
            # 채널 페이지는 탭/플레이리스트가 중첩될 수 있음
            _collect_flat_entries(e, entries_out)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for entry in entries_out:
        vid = entry["id"]
        if vid not in seen:
            seen.add(vid)
            out.append(entry)
    if max_videos:
        out = out[:max_videos]
    return out


def list_video_ids(channel_url: str, max_videos: int):
    """flat 추출로 영상 ID 목록만 빠르게 가져온다."""
    return [entry["id"] for entry in list_video_entries(channel_url, max_videos)]


def fetch_video_detail(video_id: str, with_comments: bool, max_comments: int):
    """영상 1개의 상세 메타데이터(+선택적으로 댓글)를 추출."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp가 필요합니다: pip install -r requirements.txt")
    opts = {
        "quiet": True,
        "skip_download": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "socket_timeout": 30,
    }
    if with_comments:
        opts["getcomments"] = True
        opts["extractor_args"] = {
            "youtube": {
                "max_comments": [str(max_comments), "all", str(max_comments), "all"],
                "comment_sort": ["top"],
            }
        }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}",
                                download=False)


VIDEO_FIELDS = [
    "id", "title", "upload_date", "duration", "view_count", "like_count",
    "comment_count", "channel", "channel_id", "categories", "tags", "webpage_url",
]


def collect_channel(channel, max_videos=100, with_comments=False,
                    comment_videos=15, max_comments=150, sleep=0.5,
                    data_dir=DATA_DIR, log=print):
    """채널 데이터를 수집해 지정한 data_dir에 CSV로 저장한다."""
    os.makedirs(data_dir, exist_ok=True)
    channel_url = normalize_channel_url(channel)
    log(f"[1/3] 영상 목록 수집: {channel_url}")
    entries = list_video_entries(channel_url, max_videos)
    log(f"      → {len(entries)}개 영상 발견")
    if not entries:
        raise RuntimeError("영상을 찾지 못했습니다. URL/핸들을 확인하세요.")

    log(f"[2/3] 영상 상세 수집 (댓글 수집={with_comments})")
    videos = []
    comments_rows = []
    failures = []
    used_flat_fallback = 0
    for i, flat in enumerate(entries, 1):
        vid = flat["id"]
        want_comments = with_comments and i <= comment_videos
        had_detail_error = False
        try:
            info = fetch_video_detail(vid, False, max_comments)
        except Exception as e:
            message = f"{vid} 상세정보 실패: {e}"
            failures.append(message)
            log(f"      ! {message}")
            had_detail_error = True
            info = None
        if not info and not had_detail_error:
            message = f"{vid} 상세정보 실패: yt-dlp가 빈 응답을 반환했습니다."
            failures.append(message)
            log(f"      ! {message}")
        if not info:
            info = flat
            used_flat_fallback += 1
        row = {k: info.get(k) for k in VIDEO_FIELDS}
        row["id"] = row.get("id") or vid
        row["webpage_url"] = row.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        if isinstance(row.get("categories"), list):
            row["categories"] = "|".join(row["categories"] or [])
        if isinstance(row.get("tags"), list):
            row["tags"] = "|".join(row["tags"] or [])
        videos.append(row)

        if want_comments:
            try:
                comment_info = fetch_video_detail(vid, True, max_comments)
                for c in ((comment_info or {}).get("comments") or []):
                    comments_rows.append({
                        "video_id": vid,
                        "video_title": info.get("title"),
                        "author": c.get("author"),
                        "text": (c.get("text") or "").replace("\n", " ").strip(),
                        "like_count": c.get("like_count"),
                        "timestamp": c.get("timestamp"),
                    })
            except Exception as e:
                log(f"      ! {vid} 댓글 수집 실패: {e}")
        log(f"      [{i}/{len(entries)}] {str(info.get('title'))[:40]}  "
            f"views={info.get('view_count')}")
        time.sleep(sleep)

    if not videos:
        detail = " / ".join(failures[:3]) if failures else "상세 실패 로그 없음"
        raise RuntimeError(
            "영상 상세 정보를 수집하지 못했습니다. "
            "채널 URL이 맞는지 확인하고, Render 같은 클라우드에서는 YouTube가 요청을 막을 수 있습니다. "
            f"첫 실패: {detail}"
        )
    if used_flat_fallback:
        log(f"      ! 상세정보 실패 {used_flat_fallback}건은 채널 목록의 기본 정보로 대체했습니다.")

    log("[3/3] CSV 저장")
    videos_path = os.path.join(data_dir, "videos.csv")
    with open(videos_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=VIDEO_FIELDS)
        w.writeheader()
        w.writerows(videos)
    log(f"      저장: {videos_path}  ({len(videos)}행)")

    comments_path = None
    if comments_rows:
        comments_path = os.path.join(data_dir, "comments.csv")
        with open(comments_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(
                f, fieldnames=["video_id", "video_title", "author", "text",
                               "like_count", "timestamp"])
            w.writeheader()
            w.writerows(comments_rows)
        log(f"      저장: {comments_path}  ({len(comments_rows)}행)")

    return {
        "channel_url": channel_url,
        "video_count": len(videos),
        "comment_count": len(comments_rows),
        "videos_path": videos_path,
        "comments_path": comments_path,
    }


def main():
    ap = argparse.ArgumentParser(description="유튜브 채널 데이터 수집기 (yt-dlp)")
    ap.add_argument("channel", help="채널 URL, @핸들, 또는 채널ID(UC...)")
    ap.add_argument("--max-videos", type=int, default=100,
                    help="수집할 최대 영상 수 (기본 100)")
    ap.add_argument("--with-comments", action="store_true",
                    help="댓글도 수집 (느림)")
    ap.add_argument("--comment-videos", type=int, default=15,
                    help="댓글을 수집할 상위 영상 수 (기본 15)")
    ap.add_argument("--max-comments", type=int, default=150,
                    help="영상당 최대 댓글 수 (기본 150)")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="요청 간 대기 초 (기본 0.5, 차단 방지)")
    args = ap.parse_args()

    if yt_dlp is None:
        sys.exit("yt-dlp가 필요합니다:  pip install -r requirements.txt")

    collect_channel(
        args.channel,
        max_videos=args.max_videos,
        with_comments=args.with_comments,
        comment_videos=args.comment_videos,
        max_comments=args.max_comments,
        sleep=args.sleep,
        data_dir=DATA_DIR,
    )

    print("완료. 이제  python analyze.py  를 실행하세요.")


if __name__ == "__main__":
    main()
