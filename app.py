"""
app.py — 유튜브 채널 분석 파이프라인 웹 서버

실행:
  python app.py

브라우저:
  http://localhost:8000
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
RUNS_DIR = Path(os.environ.get("RUNS_DIR", BASE_DIR / "runs")).resolve()
SAMPLE_RUN_ID = "current"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.RLock()
PIPELINE_LOCK = threading.Lock()

CHART_LABELS = {
    "01_timeseries.png": "조회수 시계열",
    "02_duration.png": "영상 길이",
    "03_titles.png": "제목 키워드와 장르",
    "04_title_length.png": "제목 길이와 조회수",
    "05_comment_keywords.png": "댓글 키워드",
    "06_comment_wordcloud.png": "댓글 워드클라우드",
    "07_comment_sentiment.png": "댓글 감성 분포",
    "08_product_question_intents.png": "제품 관심 질문 유형",
    "09_product_keywords.png": "제품 관심 키워드",
    "10_product_wordcloud.png": "제품 관심 워드클라우드",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_slug(target: str) -> str:
    slug = target.strip().lower()
    slug = re.sub(r"https?://", "", slug)
    slug = re.sub(r"[^a-z0-9@._-]+", "-", slug)
    slug = slug.strip("-._@")
    return slug[:42] or "channel"


def validate_channel_target(target: str) -> None:
    target = target.strip()
    if not target:
        raise ValueError("채널 핸들, 채널 ID, 또는 URL을 입력하세요.")
    if target.startswith(("http://", "https://")):
        parsed = urlparse(target)
        host = parsed.netloc.lower().removeprefix("www.")
        allowed_hosts = {"youtube.com", "m.youtube.com", "youtu.be"}
        if host not in allowed_hosts and not host.endswith(".youtube.com"):
            raise ValueError("유튜브 채널 URL만 입력할 수 있습니다.")


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def clamp_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def bytes_count(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def sample_available() -> bool:
    return (BASE_DIR / "data" / "videos.csv").exists()


def run_paths(run_id: str) -> tuple[Path, Path, Path]:
    if run_id == SAMPLE_RUN_ID:
        return BASE_DIR, BASE_DIR / "data", BASE_DIR / "output"
    run_dir = RUNS_DIR / run_id
    return run_dir, run_dir / "data", run_dir / "output"


def manifest_path(run_id: str) -> Path:
    run_dir, _, _ = run_paths(run_id)
    return run_dir / "run.json"


def read_manifest(run_id: str) -> dict:
    if run_id == SAMPLE_RUN_ID:
        mtime = (BASE_DIR / "data" / "videos.csv").stat().st_mtime
        return {
            "run_id": SAMPLE_RUN_ID,
            "status": "sample",
            "created_at": datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds"),
            "completed_at": None,
            "channel_target": "현재 로컬 데이터셋",
            "options": {},
        }

    path = manifest_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"분석 기록을 찾을 수 없습니다: {run_id}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_manifest(run_id: str, payload: dict) -> None:
    path = manifest_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def append_log(job: dict, message: str) -> None:
    line = {"time": datetime.now().strftime("%H:%M:%S"), "message": str(message)}
    with JOBS_LOCK:
        job.setdefault("logs", []).append(line)
        job["updated_at"] = now_iso()


def set_job(job: dict, **updates) -> None:
    with JOBS_LOCK:
        job.update(updates)
        job["updated_at"] = now_iso()


def load_video_frame(data_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(data_dir / "videos.csv")
    if "upload_date" in df:
        dates = pd.to_numeric(df["upload_date"], errors="coerce").astype("Int64")
        df["upload_date"] = pd.to_datetime(
            dates.astype(str), format="%Y%m%d", errors="coerce"
        )
    for column in ["duration", "view_count", "like_count", "comment_count"]:
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if "duration" in df:
        df["duration_min"] = df["duration"] / 60.0
        df["is_short"] = df["duration"] <= 60
    else:
        df["duration_min"] = pd.NA
        df["is_short"] = pd.NA
    if {"like_count", "view_count"}.issubset(df.columns):
        df["like_rate"] = df["like_count"] / df["view_count"]
    else:
        df["like_rate"] = pd.NA
    return df


def clean_number(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def clean_text(value):
    if pd.isna(value):
        return None
    return str(value)


def video_record(row: pd.Series) -> dict:
    upload_date = row.get("upload_date")
    if pd.notna(upload_date):
        upload_date = upload_date.date().isoformat()
    else:
        upload_date = None
    duration = clean_number(row.get("duration"))
    return {
        "id": clean_text(row.get("id")),
        "title": clean_text(row.get("title")),
        "upload_date": upload_date,
        "duration": duration,
        "duration_min": None if duration is None else round(duration / 60, 1),
        "view_count": clean_number(row.get("view_count")),
        "like_count": clean_number(row.get("like_count")),
        "comment_count": clean_number(row.get("comment_count")),
        "like_rate": None if pd.isna(row.get("like_rate")) else round(row.get("like_rate") * 100, 2),
        "url": clean_text(row.get("webpage_url")),
    }


def channel_name(df: pd.DataFrame, fallback: str) -> str:
    if "channel" in df and df["channel"].notna().any():
        modes = df["channel"].dropna().astype(str).mode()
        if not modes.empty:
            return modes.iloc[0]
    return fallback


def count_comments(data_dir: Path) -> int:
    comments_path = data_dir / "comments.csv"
    if not comments_path.exists():
        return 0
    try:
        return len(pd.read_csv(comments_path))
    except Exception:
        return 0


def summary_stats(df: pd.DataFrame, data_dir: Path, fallback_title: str) -> dict:
    if df.empty:
        return {
            "channel": fallback_title,
            "video_count": 0,
            "comment_count": count_comments(data_dir),
        }

    views = df["view_count"] if "view_count" in df else pd.Series(dtype=float)
    dates = df["upload_date"] if "upload_date" in df else pd.Series(dtype="datetime64[ns]")
    top_video = None
    if not views.dropna().empty:
        top_video = video_record(df.loc[views.idxmax()])

    return {
        "channel": channel_name(df, fallback_title),
        "video_count": int(len(df)),
        "comment_count": count_comments(data_dir),
        "date_start": None if dates.dropna().empty else dates.min().date().isoformat(),
        "date_end": None if dates.dropna().empty else dates.max().date().isoformat(),
        "total_views": None if views.dropna().empty else int(views.sum()),
        "avg_views": None if views.dropna().empty else int(views.mean()),
        "avg_duration_min": None if df["duration_min"].dropna().empty else round(float(df["duration_min"].mean()), 1),
        "short_ratio": None if df["is_short"].dropna().empty else round(float(df["is_short"].mean() * 100), 1),
        "avg_like_rate": None if df["like_rate"].dropna().empty else round(float(df["like_rate"].mean() * 100), 2),
        "top_video": top_video,
    }


def read_text(path: Path, limit: int = 20000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def product_record(row: pd.Series) -> dict:
    return {
        "intent": clean_text(row.get("product_intent")),
        "score": clean_number(row.get("product_interest_score")),
        "terms": clean_text(row.get("product_terms")),
        "video_title": clean_text(row.get("video_title")),
        "author": clean_text(row.get("author")),
        "text": clean_text(row.get("text")),
        "like_count": clean_number(row.get("like_count")),
    }


def product_interest_result(data_dir: Path, out_dir: Path) -> dict:
    total_comments = count_comments(data_dir)
    product_path = out_dir / "product_comments.csv"
    if not product_path.exists():
        return {
            "available": False,
            "total_comments": total_comments,
            "count": 0,
            "ratio": 0,
            "intents": [],
            "examples": [],
            "markdown": "",
        }
    try:
        product = pd.read_csv(product_path, engine="python")
    except Exception:
        product = pd.DataFrame()
    count = int(len(product))
    ratio = round((count / total_comments * 100), 1) if total_comments else 0
    intents = []
    if not product.empty and "product_intent" in product:
        intents = [
            {"name": str(name), "count": int(value)}
            for name, value in product["product_intent"].value_counts().items()
        ]
    examples = []
    if not product.empty:
        sort_cols = [c for c in ["product_interest_score", "like_count"] if c in product]
        if sort_cols:
            product = product.sort_values(sort_cols, ascending=False, na_position="last")
        examples = [product_record(row) for _, row in product.head(30).iterrows()]
    return {
        "available": True,
        "total_comments": total_comments,
        "count": count,
        "ratio": ratio,
        "intents": intents,
        "examples": examples,
        "markdown": read_text(out_dir / "product_comment_examples.md"),
    }


def build_run_result(run_id: str) -> dict:
    manifest = read_manifest(run_id)
    run_dir, data_dir, out_dir = run_paths(run_id)
    df = load_video_frame(data_dir)
    stats = summary_stats(df, data_dir, manifest.get("channel_target", "채널"))

    charts = []
    if out_dir.exists():
        for path in sorted(out_dir.glob("*.png")):
            charts.append({
                "name": path.name,
                "label": CHART_LABELS.get(path.name, path.stem),
                "url": f"/files/{quote(run_id)}/output/{quote(path.name)}",
            })

    files = []
    for kind, directory in (("data", data_dir), ("output", out_dir)):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in {".csv", ".md", ".png"}:
                files.append({
                    "name": path.name,
                    "kind": kind,
                    "size": bytes_count(path),
                    "url": f"/files/{quote(run_id)}/{kind}/{quote(path.name)}?download=1",
                })

    top_videos = []
    if "view_count" in df:
        top_videos = [
            video_record(row)
            for _, row in df.sort_values("view_count", ascending=False).head(12).iterrows()
        ]

    recent_videos = []
    if "upload_date" in df:
        recent_videos = [
            video_record(row)
            for _, row in df.sort_values("upload_date", ascending=False).head(12).iterrows()
        ]

    return {
        "run_id": run_id,
        "meta": manifest,
        "stats": stats,
        "charts": charts,
        "files": files,
        "top_videos": top_videos,
        "recent_videos": recent_videos,
        "summary_markdown": read_text(out_dir / "summary.md"),
        "product_interest": product_interest_result(data_dir, out_dir),
        "is_sample": run_id == SAMPLE_RUN_ID,
        "run_dir": None if run_id == SAMPLE_RUN_ID else str(run_dir),
    }


def list_runs() -> list[dict]:
    items = []
    RUNS_DIR.mkdir(exist_ok=True)
    for path in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        run_id = path.name
        try:
            result = build_run_result(run_id)
        except Exception:
            continue
        items.append({
            "run_id": run_id,
            "channel": result["stats"].get("channel"),
            "status": result["meta"].get("status"),
            "created_at": result["meta"].get("created_at"),
            "video_count": result["stats"].get("video_count"),
            "comment_count": result["stats"].get("comment_count"),
        })

    if sample_available():
        try:
            result = build_run_result(SAMPLE_RUN_ID)
            items.append({
                "run_id": SAMPLE_RUN_ID,
                "channel": result["stats"].get("channel"),
                "status": "sample",
                "created_at": result["meta"].get("created_at"),
                "video_count": result["stats"].get("video_count"),
                "comment_count": result["stats"].get("comment_count"),
            })
        except Exception:
            pass
    return items


def run_pipeline(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]

    run_id = job["run_id"]
    options = job["options"]
    run_dir, data_dir, out_dir = run_paths(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "status": "running",
        "created_at": job["created_at"],
        "completed_at": None,
        "channel_target": options["channel"],
        "options": options,
    }
    write_manifest(run_id, manifest)

    try:
        set_job(job, status="running", progress=3)
        append_log(job, "파이프라인 대기열에 진입했습니다.")
        with PIPELINE_LOCK:
            append_log(job, "수집을 시작합니다.")
            set_job(job, progress=8)
            from collect import collect_channel

            collect_result = collect_channel(
                options["channel"],
                max_videos=options["max_videos"],
                with_comments=options["with_comments"],
                comment_videos=options["comment_videos"],
                max_comments=options["max_comments"],
                sleep=options["sleep"],
                data_dir=str(data_dir),
                log=lambda message: append_log(job, message),
            )

            set_job(job, progress=62)
            append_log(job, "차트와 요약 리포트를 생성합니다.")
            import analyze

            df = analyze.load_videos(str(data_dir))
            set_job(job, progress=68)
            append_log(job, "조회수 시계열 분석")
            analyze.analyze_timeseries(df, str(out_dir))
            set_job(job, progress=74)
            append_log(job, "영상 길이 분석")
            analyze.analyze_duration(df, str(out_dir))
            set_job(job, progress=80)
            append_log(job, "제목과 장르 분석")
            analyze.analyze_titles(df, str(out_dir))
            set_job(job, progress=86)
            append_log(job, "댓글 텍스트 분석")
            analyze.analyze_comments(str(data_dir), str(out_dir))
            set_job(job, progress=90)
            append_log(job, "제품 관심 댓글 분석")
            analyze.analyze_product_comments(str(data_dir), str(out_dir))
            set_job(job, progress=94)
            append_log(job, "요약 리포트 작성")
            analyze.write_summary(df, str(data_dir), str(out_dir))

            manifest.update({
                "status": "complete",
                "completed_at": now_iso(),
                "collect_result": collect_result,
            })
            result = build_run_result(run_id)
            manifest["channel"] = result["stats"].get("channel")
            manifest["video_count"] = result["stats"].get("video_count")
            manifest["comment_count"] = result["stats"].get("comment_count")
            write_manifest(run_id, manifest)

        append_log(job, "완료되었습니다.")
        set_job(job, status="complete", progress=100, result=build_run_result(run_id))
    except Exception as exc:
        error = str(exc)
        append_log(job, f"실패: {error}")
        manifest.update({
            "status": "failed",
            "completed_at": now_iso(),
            "error": error,
            "traceback": traceback.format_exc(),
        })
        write_manifest(run_id, manifest)
        set_job(job, status="failed", error=error)


def start_job(payload: dict) -> dict:
    channel = str(payload.get("channel", "")).strip()
    validate_channel_target(channel)

    options = {
        "channel": channel,
        "max_videos": clamp_int(payload.get("maxVideos"), 80, 1, 500),
        "with_comments": bool(payload.get("withComments")),
        "comment_videos": clamp_int(payload.get("commentVideos"), 10, 1, 100),
        "max_comments": clamp_int(payload.get("maxComments"), 100, 1, 500),
        "sleep": clamp_float(payload.get("sleep"), 0.5, 0.0, 5.0),
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{run_slug(channel)}"
    job_id = f"job-{stamp}-{int(time.time() * 1000) % 100000}"
    job = {
        "job_id": job_id,
        "run_id": run_id,
        "status": "queued",
        "progress": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "options": options,
        "logs": [],
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=run_pipeline, args=(job_id,), daemon=True)
    thread.start()
    return job


class AppHandler(BaseHTTPRequestHandler):
    server_version = "YouTubePipeline/1.0"

    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, status=HTTPStatus.OK, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path: Path, content_type=None, download=False):
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=30")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self.serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/healthz":
            self.send_json({"ok": True, "time": now_iso()})
            return
        if path.startswith("/static/"):
            name = unquote(path.removeprefix("/static/"))
            safe = Path(name)
            if safe.is_absolute() or ".." in safe.parts:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            self.serve_file(WEB_DIR / "static" / safe)
            return
        if path == "/api/runs":
            self.send_json({"runs": list_runs()})
            return
        if path.startswith("/api/runs/"):
            run_id = unquote(path.removeprefix("/api/runs/"))
            try:
                self.send_json(build_run_result(run_id))
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/jobs/"):
            job_id = unquote(path.removeprefix("/api/jobs/"))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = dict(job) if job else None
            if not payload:
                self.send_json({"error": "작업을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(payload)
            return
        if path.startswith("/files/"):
            parts = path.split("/", 4)
            if len(parts) != 5:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            _, _, run_id_raw, kind, filename_raw = parts
            run_id = unquote(run_id_raw)
            filename = os.path.basename(unquote(filename_raw))
            if kind not in {"data", "output"} or not filename:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            _, data_dir, out_dir = run_paths(run_id)
            directory = data_dir if kind == "data" else out_dir
            self.serve_file(directory / filename, download=query.get("download") == ["1"])
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/analyze":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw or "{}")
            job = start_job(payload)
            self.send_json(job, HTTPStatus.ACCEPTED)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"유튜브 분석 파이프라인 웹사이트: http://{host}:{port}")
    print("종료하려면 Ctrl+C")
    server.serve_forever()


if __name__ == "__main__":
    main()
