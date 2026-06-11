# 📈 포트폴리오 일일 주식 브리핑 — GitHub Actions 자동화

매일 평일 오전 11시 (KST) 자동으로 주가 + 뉴스 브리핑을 Gmail로 발송합니다.

---

## 📁 파일 구조

```
stock-briefing/
├── .github/
│   └── workflows/
│       └── daily_briefing.yml   ← GitHub Actions 스케줄
├── src/
│   └── briefing.py              ← 메인 실행 스크립트
├── requirements.txt             ← 의존 라이브러리
└── README.md
```

---

## 🚀 설정 방법 (5단계)

### Step 1 — GitHub 레포 만들기

1. https://github.com/new 접속
2. Repository name: `stock-briefing` (또는 원하는 이름)
3. **Private** 선택 (권장 — 이메일 정보 보호)
4. **Create repository** 클릭

---

### Step 2 — 파일 업로드

레포 메인 페이지에서 **"uploading an existing file"** 클릭 후 아래 구조대로 파일 업로드:

```
.github/workflows/daily_briefing.yml
src/briefing.py
requirements.txt
README.md
```

> 폴더 구조가 없으면 파일명에 경로를 직접 입력하면 됩니다.
> 예: `.github/workflows/daily_briefing.yml`

---

### Step 3 — Gmail 앱 비밀번호 발급

> Gmail 2단계 인증이 반드시 켜져 있어야 합니다.

1. https://myaccount.google.com/security 접속
2. **2단계 인증** → 켜기 (이미 켜져 있으면 패스)
3. https://myaccount.google.com/apppasswords 접속
4. 앱 선택: **기타 (직접 입력)** → `StockBriefing` 입력
5. **생성** 클릭 → 16자리 비밀번호 복사해두기 (다시 볼 수 없음!)

---

### Step 4 — GitHub Secrets 등록

레포 페이지 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

아래 5개를 하나씩 등록:

| Secret 이름 | 값 |
|---|---|
| `SENDER_EMAIL` | 발신 Gmail 주소 (예: you@gmail.com) |
| `SENDER_PASSWORD` | Step 3에서 발급한 16자리 앱 비밀번호 |
| `RECIPIENT_EMAIL` | 수신 이메일 (예: gatnet19@hanmail.net) |
| `NAVER_CLIENT_ID` | `wFIUuQUBy1ScZxBZm0Nx` |
| `NAVER_CLIENT_SECRET` | `ze_U2EKLje` |

---

### Step 5 — 테스트 실행

레포 → **Actions** 탭 → **📈 Daily Stock Briefing** → **Run workflow** → **Run workflow** 버튼 클릭

초록색 체크(✅)가 뜨면 성공 — 이메일 확인!

---

## ⏰ 자동 실행 스케줄

```yaml
cron: '0 2 * * 1-5'   # 평일 UTC 02:00 = KST 11:00
```

> GitHub Actions cron은 UTC 기준입니다. 한국 시간(KST)은 UTC+9 이므로 KST 11시 = UTC 02시.

---

## 🛠 종목 변경 방법

`src/briefing.py` 상단 `PORTFOLIO` 딕셔너리 수정:

```python
PORTFOLIO = {
    "DIS":  "디즈니",
    "AAPL": "애플",
    "MSFT": "마이크로소프트",
    # "TSLA": "테슬라",   ← 추가 예시
    # "NVDA": "엔비디아", ← 추가 예시
}
```

---

## ❓ 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| Actions가 실행 안 됨 | 레포 비활성화 | Actions 탭 → Enable 클릭 |
| 이메일 인증 오류 | 앱 비밀번호 틀림 | Step 3 다시 진행 |
| 주가 N/A | yfinance 일시 장애 | 다음날 자동 복구 |
| Naver 뉴스 없음 | API 한도 초과 | 네이버 개발자센터 확인 |
