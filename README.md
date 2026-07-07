# 유튜브 채널 분석 도구

API 키 없이 **yt-dlp**로 채널 데이터를 수집해, 댓글 텍스트 마이닝·조회수 시계열·영상 길이·제목/장르를 분석합니다. 한국어에 맞춰 한글 폰트와 명사 추출을 지원합니다.

## 설치
```bash
pip install -r requirements.txt
# (선택) 명사 추출 정확도 ↑ — Java 설치 후
pip install konlpy
```

## 웹 파이프라인 실행
```bash
python app.py
```
브라우저에서 `http://localhost:8000`을 열면 다른 유튜버의 `@핸들`, 채널 ID(`UC...`), 또는 채널 URL을 입력해 수집·분석·리포트 생성을 한 번에 실행할 수 있습니다.

- 실행 결과는 `runs/분석ID/data/`, `runs/분석ID/output/`에 채널별로 분리 저장됩니다.
- 웹 화면에서 진행 로그, 차트, 조회수 상위 영상, 최근 업로드 영상, CSV/Markdown 다운로드를 확인할 수 있습니다.
- 댓글 수집은 오래 걸릴 수 있으므로 웹 옵션에서 댓글 영상 수와 영상당 댓글 수를 조절하세요.
- `YOUTUBE_API_KEY` 환경변수가 있으면 yt-dlp 대신 공식 YouTube Data API v3로 수집합니다.
- 댓글이 있으면 제품 구매처·가격·브랜드·사용감 질문을 자동 추출해 제품 관심 댓글 탭에서 보여줍니다. 채널 주인 댓글은 제외해 시청자 질문 중심으로 분석합니다.

## YouTube Data API 키 사용
Render 같은 클라우드에서 yt-dlp가 막히면 공식 API 키 방식을 사용하세요.

1. Google Cloud Console에서 프로젝트를 만들거나 기존 프로젝트를 선택합니다.
2. **APIs & Services → Library**에서 **YouTube Data API v3**를 Enable 합니다.
3. **APIs & Services → Credentials → Create credentials → API key**를 선택합니다.
4. Render 서비스의 **Environment**에 `YOUTUBE_API_KEY`를 추가하고 값을 API 키로 설정합니다.
5. Render에서 **Manual Deploy → Deploy latest commit**을 실행합니다.

공식 API 경로는 `channels.list`로 채널과 업로드 플레이리스트를 찾고, `playlistItems.list`로 업로드 영상 목록을 가져온 뒤, `videos.list`로 조회수/좋아요/댓글수/길이를 채웁니다. 댓글 포함 옵션은 `commentThreads.list`를 사용합니다.

### API 사용량 계산
YouTube Data API는 대부분의 `list` 호출이 1 unit입니다. 이 앱은 대략 아래처럼 사용합니다.

- 기본 메타데이터: `channels.list` 1회 + `playlistItems.list` 페이지 수 + `videos.list` 페이지 수
- 댓글: 댓글 분석 영상 수 × `ceil(영상당 댓글 수 / 100)`

현재 최대값인 영상 500개, 댓글 영상 100개, 영상당 댓글 500개로 실행하면 대략 `1 + 10 + 10 + 100×5 = 521 units`를 씁니다. 기본 일일 quota 10,000 units 기준으로 약 19회 실행하면 하루 quota가 고갈될 수 있습니다.

## 배포
이 앱은 유튜브 데이터를 수집하고 Python 분석을 실행하는 서버형 앱입니다. GitHub Pages 같은 정적 호스팅이 아니라 Docker 또는 Python 웹 서비스를 지원하는 플랫폼에 배포하세요.

### Docker로 실행
```bash
docker build -t youtube-channel-analysis .
docker run --rm -p 8000:8000 -v "$(pwd)/runs:/app/runs" youtube-channel-analysis
```

### 클라우드 배포 권장 설정
- 빌드 방식: Dockerfile
- 포트: 환경변수 `PORT` 사용
- 시작 명령: `python app.py`
- 환경변수: `HOST=0.0.0.0`, `RUNS_DIR=/app/runs`, `YOUTUBE_API_KEY=발급받은_API_키`
- 무료 테스트 배포에서는 분석 결과가 서버 재시작/재배포 때 사라질 수 있습니다.
- 결과를 계속 보관하려면 유료 persistent disk/volume을 `/app/runs`에 연결하세요.

### Render에서 배포
1. 이 폴더를 GitHub 저장소로 push합니다.
2. Render에서 **New → Blueprint**를 선택하고 저장소를 연결합니다.
3. `render.yaml`이 무료 웹 서비스와 헬스체크를 자동 설정합니다.

### Railway에서 배포
1. 이 폴더를 GitHub 저장소로 push합니다.
2. Railway에서 **New Project → Deploy from GitHub repo**를 선택합니다.
3. `railway.json`과 `Dockerfile`이 자동으로 빌드/헬스체크를 설정합니다.

### 배포 전 주의
- 댓글 수집은 오래 걸리고 요청량이 많습니다. 공개 서비스에서는 기본 영상 수와 댓글 수를 낮게 잡는 것이 좋습니다.
- yt-dlp 기반 수집은 유튜브 페이지 구조나 차단 정책 변화의 영향을 받을 수 있습니다.
- 여러 사람이 동시에 실행하면 서버 자원을 많이 씁니다. 현재 앱은 한 번에 한 분석만 실행하도록 잠금 처리되어 있습니다.

### 자주 보는 실패
- `영상 상세 정보를 수집하지 못했습니다.`: 영상 목록 ID는 찾았지만 개별 영상 상세 페이지를 하나도 가져오지 못한 상태입니다. 채널 주소가 맞는지 확인하고, 먼저 댓글 포함을 끈 뒤 영상 수를 1~5개로 낮춰 테스트하세요. Render 같은 무료 클라우드에서는 YouTube가 데이터센터 IP 요청을 제한할 수 있습니다.
- 댓글 수집만 실패하는 경우에도 영상 메타데이터 분석은 계속 진행됩니다. 댓글 분석이 꼭 필요하면 댓글 영상 수와 영상당 댓글 수를 줄이고 다시 실행하세요.

## 1) 데이터 수집
```bash
# 기본: 최근 100개 영상 메타데이터만
python collect.py "@채널핸들"

# 댓글까지 (상위 20개 영상, 영상당 최대 200개)
python collect.py "@채널핸들" --max-videos 150 --with-comments --comment-videos 20 --max-comments 200
```
채널은 `@핸들`, 채널ID(`UC...`), 전체 URL 모두 인식합니다. 결과는 `data/videos.csv`, `data/comments.csv`로 저장됩니다.

## 2) 분석
```bash
python analyze.py
```
`output/` 폴더에 차트와 `summary.md`가 생성됩니다.

| 파일 | 내용 |
|------|------|
| `01_timeseries.png` | 업로드일별 조회수, 월별 평균, 업로드 편수, 누적 조회수 |
| `02_duration.png` | 길이 분포, 길이 vs 조회수, 숏폼 vs 롱폼 |
| `03_titles.png` | 제목 키워드 상위 20, 카테고리(장르) 분포 |
| `04_title_length.png` | 제목 길이 vs 조회수 |
| `05_comment_keywords.png` | 댓글 키워드 상위 25 |
| `06_comment_wordcloud.png` | 댓글 워드클라우드 |
| `07_comment_sentiment.png` | 댓글 단순 감성 분포 |
| `08_product_question_intents.png` | 제품 관심 질문 유형, 제품 질문이 많은 영상 |
| `09_product_keywords.png` | 제품 관심 댓글 키워드 |
| `10_product_wordcloud.png` | 제품 관심 댓글 워드클라우드 |
| `product_comments.csv` | 제품 관심 댓글 추출 결과 |
| `product_comment_examples.md` | 대표 제품 질문 댓글 예시 |

## 3) 노트북
셀 단위로 살펴보려면 `youtube_analysis.ipynb`를 여세요.

## 파일 구성
```
collect.py              데이터 수집 (yt-dlp)
analyze.py              전체 분석 + 차트 생성
app.py                  웹 파이프라인 서버
web/                    웹 UI
koreatext.py            한글 폰트 탐색 · 토크나이저 · 단순 감성사전
youtube_analysis.ipynb  주피터 노트북
requirements.txt        의존성
```

## 참고 / 한계
- yt-dlp의 `view_count`는 **수집 시점의 누적 조회수 스냅샷**입니다. 특정 영상의 시간별 조회수 곡선은 유튜브가 공개하지 않으므로, 시계열은 **업로드일 기준 집계**로 봅니다. 진짜 시간별 추이가 필요하면 `collect.py`를 주기적으로(예: 매일) 돌려 스냅샷을 쌓으세요.
- 댓글 수집은 느립니다. `--comment-videos`, `--max-comments`로 범위를 조절하세요.
- `konlpy` 미설치 시 정규식 기반(2글자 이상 한글 어절)으로 폴백합니다. 불용어는 `koreatext.py`의 `DEFAULT_STOPWORDS`에서 조정하세요.
- 과도한 요청은 차단될 수 있으니 `--sleep` 값을 늘리세요.
