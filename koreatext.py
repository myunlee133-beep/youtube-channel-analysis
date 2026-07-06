"""
koreatext.py — 한국어 텍스트 처리 & 한글 폰트 유틸

- find_korean_font(): matplotlib / wordcloud 용 한글 폰트 경로 자동 탐색
- tokenize(text): konlpy(Okt)가 있으면 명사 추출, 없으면 정규식 기반 폴백
- DEFAULT_STOPWORDS: 기본 불용어
"""
import os
import re
import glob
import shutil
import subprocess

# ---- 한글 폰트 탐색 -------------------------------------------------
_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/AppleGothic.ttf",
    # Windows
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
]


def find_korean_font():
    """존재하는 첫 번째 한글 폰트 경로를 반환. 없으면 None."""
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    # 마지막 시도: 시스템에서 Nanum/Noto CJK 검색
    for pattern in ("/usr/share/fonts/**/*Nanum*.ttf",
                    "/usr/share/fonts/**/*CJK*.ttc",
                    "/usr/share/fonts/**/*CJK*kr*.otf"):
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None


# ---- 토크나이저 ----------------------------------------------------
DEFAULT_STOPWORDS = set("""
그리고 그러나 그래서 하지만 그런데 정말 진짜 너무 완전 그냥 이거 저거 그거
있다 없다 하다 되다 이다 이런 저런 그런 근데 아니 그게 이게 저게 여기 거기
제가 저는 나는 우리 너무너무 ㅋㅋ ㅋㅋㅋ ㅎㅎ 영상 채널 구독 좋아요 오늘 진심
""".split())


def _load_okt():
    if shutil.which("java") is None:
        return None
    try:
        subprocess.run(
            ["java", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=True,
        )
    except Exception:
        return None
    try:
        from konlpy.tag import Okt  # noqa
        return Okt()
    except Exception:
        return None


_OKT = _load_okt()
_HANGUL_WORD = re.compile(r"[가-힣]{2,}")


def tokenize(text, stopwords=None, use_nouns=True):
    """텍스트 → 토큰 리스트.
    konlpy 설치 시 명사 추출, 없으면 2글자 이상 한글 어절.
    """
    if not text:
        return []
    stop = DEFAULT_STOPWORDS if stopwords is None else stopwords
    if _OKT is not None and use_nouns:
        try:
            tokens = _OKT.nouns(text)
        except Exception:
            tokens = _HANGUL_WORD.findall(text)
    else:
        tokens = _HANGUL_WORD.findall(text)
    return [t for t in tokens if len(t) >= 2 and t not in stop]


# ---- 아주 단순한 감성 사전(참고용) ---------------------------------
POSITIVE = set("""
좋아요 좋다 최고 대박 감사 감동 사랑 유익 도움 재밌 재미 훌륭 짱 굿 인정
멋지 예쁘 응원 기대 만족 완벽 꿀팁 유용 행복 웃김 귀엽
""".split())
NEGATIVE = set("""
별로 싫다 최악 실망 별루 노잼 지루 아쉽 화나 짜증 광고 거르 손절 억지
불편 과장 낚시 어그로 논란 비추 별로임
""".split())


def sentiment_score(text):
    """+1/-1 단순 합산 점수. 0이면 중립. (정밀 분석용 아님, 감 잡기용)"""
    pos = sum(1 for w in POSITIVE if w in text)
    neg = sum(1 for w in NEGATIVE if w in text)
    return pos - neg
