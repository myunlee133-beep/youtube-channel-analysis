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
import re
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


PRODUCT_INTENT_RULES = {
    "구매처/링크": [
        "어디서", "어디꺼", "어디 거", "구매처", "구입처", "링크", "정보",
        "주문", "파나요", "파는", "판매", "공구", "스토어",
        "쿠팡", "마켓컬리", "컬리", "올리브영", "무신사", "지그재그", "에이블리",
    ],
    "가격/할인": [
        "가격", "얼마예요", "얼마에요", "얼마인가요", "얼마죠", "몇 원", "몇원",
        "비싸", "저렴", "싸게", "할인",
        "세일", "쿠폰", "가성비", "할인코드", "코드",
    ],
    "브랜드/제품명": [
        "브랜드", "제품명", "상품명",
        "무슨 제품", "무슨 브랜드", "가게 이름", "제품 이름", "브랜드 이름",
        "모델명", "품번", "컬러", "색상",
        "호수", "몇 호", "사이즈",
    ],
    "사용감/품질": [
        "지속력", "발색", "커버력", "밀착", "촉촉", "건조", "무너짐",
        "핏", "재질", "맛", "식감", "냄새", "효과", "성분",
        "칼로리", "유통기한", "배송", "보관", "매워", "달아요", "느끼",
    ],
    "입점/재고": [
        "올리브영", "쿠팡", "컬리", "마켓컬리", "스마트스토어", "편의점",
        "마트", "백화점", "매장", "오프라인", "온라인", "품절", "재입고",
        "배송", "해외배송",
    ],
    "공구/협찬": [
        "공구", "공동구매", "협찬", "광고", "내돈내산", "할인코드",
        "이벤트", "링크",
    ],
}

PRODUCT_ANCHORS = [
    "제품", "상품", "브랜드", "옷", "상의", "하의", "바지", "치마", "원피스",
    "가방", "신발", "모자", "니트", "자켓", "코트", "쿠션", "파데",
    "파운데이션", "선크림", "크림", "세럼", "향수", "렌즈", "삼겹살", "고기",
    "소스", "음식", "메뉴", "떡볶이", "치킨", "빵", "과자", "음료",
    "간식", "곱창", "두쫀쿠", "접시", "이불", "티켓",
]
QUESTION_MARKERS = [
    "?", "뭐", "무슨", "어디", "어떤", "얼마", "알려", "궁금", "있나요",
    "되나요", "인가요", "시나요", "셨나요", "추천",
    "까요", "돼요", "시켜요", "싶어", "방법",
]
PRODUCT_KEYWORD_STOPWORDS = set("""
제품 상품 브랜드 어디서 어디꺼 어디 구매처 구입처 링크 정보 가격 얼마 할인 세일
쿠폰 공구 공동구매 협찬 광고 내돈내산 이름 무슨 뭐예요 뭐에요 뭔가요 사용감
지속력 발색 사이즈 맛 배송 영상 댓글 언니 오빠 진짜 너무 혹시 알려주세요 궁금해요
""".split())
PRODUCT_INTEREST_STATEMENT_RE = re.compile(
    r"(사고\s*싶|먹고\s*싶|구매하고\s*싶|주문하려|사려구|사려고|"
    r"살래|사야겠|살\s*방법|살\s*수|링크\s*(주세요|부탁)|"
    r"정보\s*(좀|주세요|부탁)|알려\s*주)"
)


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


def monthly_view_summary(d):
    """pandas 버전에 따라 월말 offset alias가 M 또는 ME로 갈려 폴백 처리."""
    series = d.set_index("upload_date")["view_count"]
    for freq in ("ME", "M"):
        try:
            return series.resample(freq).agg(["mean", "count"])
        except ValueError as exc:
            if "Invalid frequency" not in str(exc) and "no longer supported" not in str(exc):
                raise
    return series.resample("MS").agg(["mean", "count"])


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
    monthly = monthly_view_summary(d)
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


# ---- 5) 제품 관심 댓글 분석 ----------------------------------------
def product_interest_score(text):
    text = str(text or "").strip()
    if not text or text.lower() == "nan":
        return 0, [], []

    compact = re.sub(r"\s+", "", text)
    match_text = re.sub(r"얼마(만|나)", "", text)
    match_compact = re.sub(r"얼마(만|나)", "", compact)
    matched_intents = []
    matched_terms = []
    score = 0

    direct_patterns = {
        "구매처/링크": [
            r"어디(서|꺼|\s*거).*(사|샀|사셨어|구매|주문|시켰|시켜|시키|팔|파|제품|거|꺼)",
            r"어디(꺼|\s*거)\s*(예요|에요|인가요|죠|\?)",
            r"(구매처|구입처|판매처|파는\s*곳|살\s*수|구할\s*수|파나요|공구|공동구매)",
            r"(링크|정보)\s*(좀|알려|부탁|있나요|주세요)",
        ],
        "가격/할인": [
            r"(가격|몇\s*원|몇원|할인|세일|쿠폰|싸게)",
            r"얼마\s*(예요|에요|인가요|죠|야|인지|입니까|\?)",
            r"싸게.*(살|사는|구매|방법|수)",
        ],
        "브랜드/제품명": [
            r"(브랜드|제품명|상품명|모델명|품번|가게\s*이름|제품\s*이름|브랜드\s*이름)",
            r"무슨\s*(제품|브랜드|곱창|옷|립|쿠션|파데|소스|고기|메뉴)",
            r"(옷|제품|상품|색상|컬러|호수|사이즈)\s*정보",
        ],
        "사용감/품질": [
            r"(지속력|발색|커버력|밀착|무너짐|핏|재질|식감|성분|칼로리).*(어때|어떤|괜찮|좋나|되나|돼|나요|얼마나|\?)",
            r"(맵기|배송|보관).*(어때|어떤|괜찮|좋나|되나|돼|나요|\?)",
            r"((무슨|어떤)\s*맛|맛\s*(어때|어떤|괜찮|좋나|나요|\?))",
        ],
        "입점/재고": [
            r"(올리브영|쿠팡|컬리|마켓컬리|스마트스토어|편의점|마트|백화점|매장|온라인|오프라인).*(있나요|파나요|입점|재고|구매|살\s*수)",
            r"(품절|재입고|해외배송).*(인가요|되나요|언제|\?)",
        ],
        "공구/협찬": [
            r"(공구|공동구매|협찬|광고|내돈내산|할인코드|이벤트).*(인가요|예요|에요|있나요|언제|\?)",
        ],
    }
    direct_intents = []
    for intent, patterns in direct_patterns.items():
        if any(re.search(pattern, match_text) for pattern in patterns):
            direct_intents.append(intent)

    for intent, terms in PRODUCT_INTENT_RULES.items():
        hits = [term for term in terms if term in match_text or term in match_compact]
        if intent == "가격/할인" and "얼마" in match_text and not re.search(r"얼마\s*(예요|에요|인가요|죠|야|인지|입니까|\?)", match_text):
            hits = [term for term in hits if not term.startswith("얼마")]
        if hits:
            matched_intents.append(intent)
            matched_terms.extend(hits[:3])
            score += 2 if intent in {"구매처/링크", "가격/할인", "브랜드/제품명"} else 1
    for intent in direct_intents:
        if intent not in matched_intents:
            matched_intents.append(intent)
        score += 2

    if any(marker in text for marker in QUESTION_MARKERS):
        score += 1
    if any(anchor in text for anchor in PRODUCT_ANCHORS):
        score += 1
    if re.search(r"(어디|브랜드|제품|사이즈|색상|컬러|호수|가게\s*이름).*(요|까|죠|나요|세요|\?)", match_text):
        score += 2
    if re.search(r"얼마\s*(예요|에요|인가요|죠|야|인지|입니까|\?)", match_text):
        score += 2
    if re.search(r"(사고|샀|사셨어|살\s*수|구매|주문|시켜|팔|파).*(어디|링크|정보|수\s*있)", match_text):
        score += 2

    # 짧은 감탄 댓글이 우연히 한 단어만 맞는 경우를 줄인다.
    if len(text) < 6 and score < 4:
        return 0, [], []
    has_question = any(marker in text for marker in QUESTION_MARKERS)
    has_interest_statement = bool(PRODUCT_INTEREST_STATEMENT_RE.search(text))
    if not has_question and not has_interest_statement:
        return 0, [], []
    has_anchor = any(anchor in text for anchor in PRODUCT_ANCHORS)
    if not direct_intents and not (matched_intents and has_question and has_anchor):
        return 0, [], []
    if not direct_intents and set(matched_intents).issubset({"입점/재고", "사용감/품질"}):
        return 0, [], []
    if score < 3:
        return 0, [], []

    if not matched_intents:
        matched_intents = ["기타 제품 질문"]
    return score, matched_intents, sorted(set(matched_terms))


def classify_product_comment(text):
    score, intents, terms = product_interest_score(text)
    return {
        "score": score,
        "primary_intent": intents[0] if intents else "",
        "intents": "|".join(intents),
        "terms": "|".join(terms),
    }


def product_keyword_counter(texts):
    tokens = []
    for text in texts:
        tokens.extend(kt.tokenize(str(text)))
        tokens.extend(re.findall(r"[A-Za-z0-9가-힣]{2,}", str(text)))
    out = []
    for token in tokens:
        token = token.strip()
        if len(token) < 2:
            continue
        if token in PRODUCT_KEYWORD_STOPWORDS or token in kt.DEFAULT_STOPWORDS:
            continue
        if any(token in term for terms in PRODUCT_INTENT_RULES.values() for term in terms):
            continue
        out.append(token)
    return Counter(out)


def normalize_identity(value):
    value = str(value or "").strip().lower().removeprefix("@")
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def channel_identity_terms(data_dir):
    path = os.path.join(data_dir, "videos.csv")
    if not os.path.exists(path):
        return set()
    try:
        videos = pd.read_csv(path)
    except Exception:
        return set()
    terms = set()
    for channel in videos.get("channel", pd.Series(dtype=str)).dropna().astype(str).unique():
        normalized_channel = normalize_identity(channel)
        if len(normalized_channel) >= 3:
            terms.add(normalized_channel)
        for token in re.findall(r"[A-Za-z0-9가-힣]{2,}", channel):
            normalized = normalize_identity(token)
            if len(normalized) >= 2:
                terms.add(normalized)
    tag_counts = Counter()
    for tags in videos.get("tags", pd.Series(dtype=str)).dropna().astype(str):
        for tag in tags.split("|"):
            normalized = normalize_identity(tag)
            if 2 <= len(normalized) <= 20:
                tag_counts[normalized] += 1
    threshold = max(2, int(len(videos) * 0.2))
    for tag, count in tag_counts.items():
        if count >= threshold:
            terms.add(tag)
    return terms


def infer_creator_comment_mask(cm, data_dir):
    mask = pd.Series(False, index=cm.index)
    if "is_channel_owner" in cm:
        owner = cm["is_channel_owner"].astype(str).str.lower()
        mask = mask | owner.isin({"true", "1", "yes", "y"})
    if "author_channel_id" in cm:
        video_path = os.path.join(data_dir, "videos.csv")
        try:
            videos = pd.read_csv(video_path)
            channel_ids = set(videos.get("channel_id", pd.Series(dtype=str)).dropna().astype(str))
            mask = mask | cm["author_channel_id"].astype(str).isin(channel_ids)
        except Exception:
            pass

    terms = channel_identity_terms(data_dir)
    if not terms or "author" not in cm:
        return mask

    def is_creator_name(author):
        normalized = normalize_identity(author)
        if not normalized:
            return False
        if normalized in terms:
            return True
        return any(len(term) >= 3 and term in normalized for term in terms)

    return mask | cm["author"].apply(is_creator_name)


def truncate_text(text, limit=120):
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def write_product_examples(product_df, out_dir, total_comments):
    lines = ["# 제품 관심 댓글 분석\n"]
    count = len(product_df)
    ratio = (count / total_comments * 100) if total_comments else 0
    lines.append(f"- 전체 댓글 수: **{total_comments:,}**")
    lines.append(f"- 제품 관심 댓글 수: **{count:,}**")
    lines.append(f"- 제품 관심 댓글 비율: **{ratio:.1f}%**")

    lines.append("\n## 질문 유형")
    for intent, value in product_df["product_intent"].value_counts().items():
        lines.append(f"- {intent}: {int(value):,}개")

    lines.append("\n## 대표 댓글 예시")
    examples = product_df.sort_values(
        ["product_interest_score", "like_count"],
        ascending=[False, False],
        na_position="last",
    ).head(30)
    for _, row in examples.iterrows():
        title = truncate_text(row.get("video_title", ""), 45)
        text = truncate_text(row.get("text", ""), 180)
        lines.append(f"- [{row.get('product_intent', '제품 질문')}] {text}")
        if title and title != "nan":
            lines.append(f"  - 영상: {title}")

    path = os.path.join(out_dir, "product_comment_examples.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("  저장: product_comment_examples.md")


def analyze_product_comments(data_dir, out_dir):
    path = os.path.join(data_dir, "comments.csv")
    if not os.path.exists(path):
        print("  (comments.csv 없음 → 제품 관심 댓글 분석 생략)")
        return
    cm = pd.read_csv(path)
    if cm.empty or "text" not in cm:
        print("  (댓글 텍스트 없음 → 제품 관심 댓글 분석 생략)")
        return

    classified = cm["text"].astype(str).apply(classify_product_comment)
    cm["product_interest_score"] = classified.apply(lambda x: x["score"])
    cm["product_intent"] = classified.apply(lambda x: x["primary_intent"])
    cm["product_intents"] = classified.apply(lambda x: x["intents"])
    cm["product_terms"] = classified.apply(lambda x: x["terms"])
    cm["is_likely_creator_comment"] = infer_creator_comment_mask(cm, data_dir)
    product = cm[
        (cm["product_interest_score"] > 0)
        & (~cm["is_likely_creator_comment"])
    ].copy()
    for column in ["text", "video_title", "author"]:
        if column in product:
            product[column] = product[column].astype(str).str.replace(r"[\r\n]+", " ", regex=True)

    product_path = os.path.join(out_dir, "product_comments.csv")
    product.to_csv(product_path, index=False, encoding="utf-8-sig")
    print(f"  저장: product_comments.csv ({len(product)}행)")

    if product.empty:
        return

    # 유형 분포 + 영상별 제품 질문 댓글
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    intent_counts = product["product_intent"].value_counts()
    axes[0].barh(range(len(intent_counts)), intent_counts.values, color="darkcyan")
    axes[0].set_yticks(range(len(intent_counts)))
    axes[0].set_yticklabels(intent_counts.index)
    axes[0].invert_yaxis()
    axes[0].set_title("제품 관심 질문 유형")
    axes[0].set_xlabel("댓글 수")

    if "video_title" in product:
        video_counts = product["video_title"].fillna("(제목 없음)").value_counts().head(10)
        labels = [truncate_text(v, 22) for v in video_counts.index]
        axes[1].barh(range(len(video_counts)), video_counts.values, color="slateblue")
        axes[1].set_yticks(range(len(video_counts)))
        axes[1].set_yticklabels(labels)
        axes[1].invert_yaxis()
        axes[1].set_title("제품 질문이 많은 영상")
        axes[1].set_xlabel("댓글 수")
    else:
        axes[1].text(0.5, 0.5, "영상 제목 데이터 없음", ha="center")
        axes[1].set_axis_off()
    savefig(fig, out_dir, "08_product_question_intents.png")

    # 제품 관심 댓글 키워드
    freq = product_keyword_counter(product["text"].astype(str).tolist())
    top = freq.most_common(25)
    fig, ax = plt.subplots(figsize=(8, 7))
    if top:
        words, counts = zip(*top)
        ax.barh(range(len(words)), counts, color="seagreen")
        ax.set_yticks(range(len(words)))
        ax.set_yticklabels(words)
        ax.invert_yaxis()
        ax.set_title("제품 관심 댓글 키워드")
        ax.set_xlabel("빈도")
    savefig(fig, out_dir, "09_product_keywords.png")

    try:
        from wordcloud import WordCloud
        if FONT_PATH and freq:
            wc = WordCloud(font_path=FONT_PATH, width=1000, height=600,
                           background_color="white",
                           colormap="Dark2").generate_from_frequencies(freq)
            fig, ax = plt.subplots(figsize=(11, 6.5))
            ax.imshow(wc, interpolation="bilinear")
            ax.axis("off")
            ax.set_title("제품 관심 댓글 워드클라우드")
            savefig(fig, out_dir, "10_product_wordcloud.png")
    except ImportError:
        print("  (wordcloud 미설치 → 제품 워드클라우드 생략)")

    write_product_examples(product, out_dir, len(cm))


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
    product_path = os.path.join(out_dir, "product_comments.csv")
    comments_path = os.path.join(data_dir, "comments.csv")
    if os.path.exists(product_path):
        try:
            product_n = len(pd.read_csv(product_path, engine="python"))
            total_comments = len(pd.read_csv(comments_path)) if os.path.exists(comments_path) else 0
            ratio = (product_n / total_comments * 100) if total_comments else 0
            lines.append(f"- 제품 관심 댓글: {product_n:,}개 ({ratio:.1f}%)")
        except Exception:
            pass
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
    print("5) 제품 관심 댓글")
    analyze_product_comments(data_dir, out_dir)
    print("요약 작성")
    write_summary(df, data_dir, out_dir)
    print(f"\n완료 → {out_dir}")


if __name__ == "__main__":
    main()
