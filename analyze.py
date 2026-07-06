"""
analyze.py — 수집한 유튜브 데이터 종합 분석

입력 : data/videos.csv  (필수),  data/comments.csv (있으면 댓글 분석)
출력 : output/ 폴더에 차트 PNG들 + summary.md

분석 항목
  1) 조회수 시계열      : 업로드일 기준 조회수 추이 / 월별 집계 / 누적
  2) 영상 길이 분석      : 길이 분포, 길이 vs 조회수, 숏폼 vs 롱폼
  3) 제목·장르 분석      : 제목 키워드 빈도, 카테고리 분포, 제목 길이 vs 조회수
  4) 댓글 텍스트 마이닝  : 명사 빈도, 워드클라우드, 단순 감성 분포

사용:  python analyze.py            (기본 data/ , output/)
       python analyze.py --data-dir data --out-dir output
"""
import argparse
import os
import tempfile
from collections import Counter

import pandas as pd
import numpy as np

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "youtube_channel_analysis_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_CACHE_DIR, "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", _CACHE_DIR)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

import koreatext as kt

# ---- 한글 폰트 설정 -------------------------------------------------
FONT_PATH = kt.find_korean_font()
if FONT_PATH:
    try:
        font_manager.fontManager.addfont(FONT_PATH)
        plt.rcParams["font.family"] = font_manager.FontProperties(
            fname=FONT_PATH).get_name()
    except Exception:
        pass
plt.rcParams["axes.unicode_minus"] = False


def savefig(fig, out_dir, name):
    path = os.path.join(out_dir, name)
    fig.tight_layout()
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"  저장: {name}")


# ---- 데이터 로드 & 파생 컬럼 ---------------------------------------
def load_videos(data_dir):
    path = os.path.join(data_dir, "videos.csv")
    df = pd.read_csv(path)
    df["upload_date"] = pd.to_datetime(
        df["upload_date"].astype("Int64").astype(str),
        format="%Y%m%d", errors="coerce")
    for c in ["duration", "view_count", "like_count", "comment_count"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["duration_min"] = df["duration"] / 60.0
    df["title_len"] = df["title"].astype(str).str.len()
    df["is_short"] = df["duration"] <= 60
    # 좋아요/조회수 참여율
    df["like_rate"] = df["like_count"] / df["view_count"]
    return df.sort_values("upload_date")


# ---- 1) 조회수 시계열 ----------------------------------------------
def analyze_timeseries(df, out_dir):
    d = df.dropna(subset=["upload_date", "view_count"])
    if d.empty:
        return
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    # (a) 업로드일별 조회수 산점 + 30일 이동평균
    ax = axes[0, 0]
    ax.scatter(d["upload_date"], d["view_count"], s=18, alpha=0.5)
    roll = d.set_index("upload_date")["view_count"].rolling("30D").mean()
    ax.plot(roll.index, roll.values, color="crimson", lw=2, label="30일 이동평균")
    ax.set_title("업로드일별 조회수 (스냅샷)")
    ax.set_ylabel("조회수")
    ax.legend()

    # (b) 월별 평균/합계 조회수
    ax = axes[0, 1]
    monthly = d.set_index("upload_date").resample("M")["view_count"].agg(
        ["mean", "count"])
    ax.bar(monthly.index, monthly["mean"], width=20, alpha=0.7)
    ax.set_title("월별 평균 조회수")
    ax.set_ylabel("평균 조회수")

    # (c) 월별 업로드 개수
    ax = axes[1, 0]
    ax.bar(monthly.index, monthly["count"], width=20, color="seagreen", alpha=0.7)
    ax.set_title("월별 업로드 편수")
    ax.set_ylabel("영상 수")

    # (d) 누적 조회수(업로드 순)
    ax = axes[1, 1]
    ax.plot(d["upload_date"], d["view_count"].cumsum(), color="darkorange")
    ax.set_title("누적 조회수 (업로드 순서 기준)")
    ax.set_ylabel("누적 조회수")

    savefig(fig, out_dir, "01_timeseries.png")


# ---- 2) 영상 길이 ---------------------------------------------------
def analyze_duration(df, out_dir):
    d = df.dropna(subset=["duration_min"])
    if d.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))

    axes[0].hist(d["duration_min"], bins=30, color="steelblue", alpha=0.8)
    axes[0].set_title("영상 길이 분포")
    axes[0].set_xlabel("길이(분)")
    axes[0].set_ylabel("영상 수")

    dd = d.dropna(subset=["view_count"])
    axes[1].scatter(dd["duration_min"], dd["view_count"], s=18, alpha=0.5)
    axes[1].set_title("길이 vs 조회수")
    axes[1].set_xlabel("길이(분)")
    axes[1].set_ylabel("조회수")
    if len(dd) > 2:
        corr = dd["duration_min"].corr(dd["view_count"])
        axes[1].annotate(f"상관계수 r={corr:.2f}", xy=(0.05, 0.9),
                         xycoords="axes fraction")

    # 숏폼 vs 롱폼 평균 조회수
    grp = d.groupby("is_short")["view_count"].mean()
    labels = {True: "숏폼(≤60s)", False: "롱폼(>60s)"}
    axes[2].bar([labels[i] for i in grp.index], grp.values,
                color=["salmon", "cornflowerblue"])
    axes[2].set_title("숏폼 vs 롱폼 평균 조회수")
    axes[2].set_ylabel("평균 조회수")

    savefig(fig, out_dir, "02_duration.png")


# ---- 3) 제목 & 장르 -------------------------------------------------
def analyze_titles(df, out_dir):
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))

    # 제목 키워드 빈도
    tokens = []
    for t in df["title"].astype(str):
        tokens += kt.tokenize(t)
    top = Counter(tokens).most_common(20)
    if top:
        words, counts = zip(*top)
        axes[0].barh(range(len(words)), counts, color="mediumpurple")
        axes[0].set_yticks(range(len(words)))
        axes[0].set_yticklabels(words)
        axes[0].invert_yaxis()
        axes[0].set_title("제목 키워드 상위 20")
        axes[0].set_xlabel("빈도")

    # 카테고리(장르) 분포
    cats = []
    for c in df.get("categories", pd.Series(dtype=str)).astype(str):
        if c and c != "nan":
            cats += c.split("|")
    if cats:
        top_c = Counter(cats).most_common(10)
        cw, cc = zip(*top_c)
        axes[1].bar(cw, cc, color="teal", alpha=0.8)
        axes[1].set_title("카테고리(장르) 분포")
        axes[1].set_ylabel("영상 수")
        axes[1].tick_params(axis="x", rotation=30)
    else:
        axes[1].text(0.5, 0.5, "카테고리 데이터 없음", ha="center")
        axes[1].set_axis_off()

    savefig(fig, out_dir, "03_titles.png")

    # 제목 길이 vs 조회수
    d = df.dropna(subset=["title_len", "view_count"])
    if len(d) > 2:
        fig2, ax = plt.subplots(figsize=(7, 5))
        ax.scatter(d["title_len"], d["view_count"], s=18, alpha=0.5, color="darkgreen")
        corr = d["title_len"].corr(d["view_count"])
        ax.set_title(f"제목 길이 vs 조회수 (r={corr:.2f})")
        ax.set_xlabel("제목 글자 수")
        ax.set_ylabel("조회수")
        savefig(fig2, out_dir, "04_title_length.png")


# ---- 4) 댓글 텍스트 마이닝 -----------------------------------------
def analyze_comments(data_dir, out_dir):
    path = os.path.join(data_dir, "comments.csv")
    if not os.path.exists(path):
        print("  (comments.csv 없음 → 댓글 분석 생략)")
        return
    cm = pd.read_csv(path)
    if cm.empty:
        return
    texts = cm["text"].astype(str).tolist()

    # 명사 빈도
    tokens = []
    for t in texts:
        tokens += kt.tokenize(t)
    freq = Counter(tokens)
    top = freq.most_common(25)

    fig, ax = plt.subplots(figsize=(8, 7))
    if top:
        words, counts = zip(*top)
        ax.barh(range(len(words)), counts, color="indianred")
        ax.set_yticks(range(len(words)))
        ax.set_yticklabels(words)
        ax.invert_yaxis()
        ax.set_title("댓글 키워드 상위 25")
        ax.set_xlabel("빈도")
    savefig(fig, out_dir, "05_comment_keywords.png")

    # 워드클라우드
    try:
        from wordcloud import WordCloud
        if FONT_PATH and freq:
            wc = WordCloud(font_path=FONT_PATH, width=1000, height=600,
                           background_color="white",
                           colormap="viridis").generate_from_frequencies(freq)
            fig, ax = plt.subplots(figsize=(11, 6.5))
            ax.imshow(wc, interpolation="bilinear")
            ax.axis("off")
            ax.set_title("댓글 워드클라우드")
            savefig(fig, out_dir, "06_comment_wordcloud.png")
    except ImportError:
        print("  (wordcloud 미설치 → 워드클라우드 생략)")

    # 단순 감성 분포
    cm["sentiment"] = cm["text"].astype(str).apply(kt.sentiment_score)
    sent_cat = pd.cut(cm["sentiment"], bins=[-99, -1, 0, 99],
                      labels=["부정", "중립", "긍정"])
    dist = sent_cat.value_counts().reindex(["긍정", "중립", "부정"])
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.bar(dist.index, dist.values, color=["mediumseagreen", "gray", "tomato"])
    ax.set_title("댓글 단순 감성 분포 (참고용)")
    ax.set_ylabel("댓글 수")
    savefig(fig, out_dir, "07_comment_sentiment.png")


# ---- 요약 리포트 ----------------------------------------------------
def write_summary(df, data_dir, out_dir):
    lines = ["# 채널 분석 요약\n"]
    n = len(df)
    lines.append(f"- 분석 영상 수: **{n}**")
    if df["upload_date"].notna().any():
        lines.append(f"- 기간: {df['upload_date'].min().date()} ~ "
                     f"{df['upload_date'].max().date()}")
    if df["view_count"].notna().any():
        lines.append(f"- 총 조회수(스냅샷): {int(df['view_count'].sum()):,}")
        lines.append(f"- 평균 조회수: {int(df['view_count'].mean()):,}")
        top = df.loc[df['view_count'].idxmax()]
        lines.append(f"- 최고 조회수 영상: {top['title']} "
                     f"({int(top['view_count']):,}회)")
    if df["duration_min"].notna().any():
        lines.append(f"- 평균 길이: {df['duration_min'].mean():.1f}분")
        lines.append(f"- 숏폼 비율: {df['is_short'].mean()*100:.0f}%")
    if df["like_rate"].notna().any():
        lines.append(f"- 평균 좋아요율(좋아요/조회수): "
                     f"{df['like_rate'].mean()*100:.2f}%")
    lines.append("\n## 생성된 차트")
    for f in sorted(os.listdir(out_dir)):
        if f.endswith(".png"):
            lines.append(f"- {f}")
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("  저장: summary.md")


def main():
    ap = argparse.ArgumentParser(description="유튜브 채널 분석기")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out-dir", default="output")
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, args.data_dir) if not os.path.isabs(args.data_dir) else args.data_dir
    out_dir = os.path.join(base, args.out_dir) if not os.path.isabs(args.out_dir) else args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    if not FONT_PATH:
        print("경고: 한글 폰트를 찾지 못했습니다. 그래프 한글이 깨질 수 있습니다.")

    print("데이터 로드...")
    df = load_videos(data_dir)
    print(f"  영상 {len(df)}건")

    print("1) 조회수 시계열")
    analyze_timeseries(df, out_dir)
    print("2) 영상 길이")
    analyze_duration(df, out_dir)
    print("3) 제목·장르")
    analyze_titles(df, out_dir)
    print("4) 댓글 텍스트 마이닝")
    analyze_comments(data_dir, out_dir)
    print("요약 작성")
    write_summary(df, data_dir, out_dir)
    print(f"\n완료 → {out_dir}")


if __name__ == "__main__":
    main()
