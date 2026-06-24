"""
포트폴리오 일일 주식 브리핑
- yfinance 로 해외 주가 수집
- Google News RSS 로 영문 뉴스 수집
- Naver News API 로 국내 뉴스 수집
- Gmail SMTP 로 HTML 이메일 실발송
"""

import os, json, time, smtplib, feedparser, urllib.request, urllib.parse, csv, io, yfinance as yf, requests
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_YF_SESSION = requests.Session()
_YF_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
})

SENDER_EMAIL    = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]
NAVER_ID        = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_SECRET    = os.environ.get("NAVER_CLIENT_SECRET", "")

PORTFOLIO = {
    "DIS":  "디즈니",
    "AAPL": "애플",
    "MSFT": "마이크로소프트",
}

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)


# ── 1. 주가 ──────────────────────────────────────────────────
def _make_result(sym, name, prev, curr):
    chg = round((curr - prev) / prev * 100, 2)
    return dict(
        symbol=sym, name=name,
        price=round(curr, 2), prev=round(prev, 2),
        chg=chg,
        arrow="▲" if chg >= 0 else "▼",
        sign="+" if chg >= 0 else "",
        color_hex="#2e7d32" if chg >= 0 else "#c62828",
        emoji="🟢" if chg >= 0 else "🔴",
    )

def _fetch_naver(sym):
    url = f"https://m.stock.naver.com/api/stock/{sym}/basic"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://m.stock.naver.com/",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        raw_text = r.read().decode("utf-8")
    try:
        data = json.loads(raw_text)
    except Exception:
        raise ValueError(f"JSON 파싱 실패: {raw_text[:200]}")
    def to_float(v):
        if v is None: return None
        if isinstance(v, str): v = v.replace(",", "")
        return float(v)
    curr = to_float(data.get("closePrice"))
    prev = to_float(data.get("previousClose"))
    if curr is None or prev is None:
        raise ValueError(f"필드 누락. keys={list(data.keys())[:15]}")
    return prev, curr

def _fetch_yahoo_chart(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    result = data.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"Yahoo Chart 응답 오류: {data.get('chart', {}).get('error')}")
    closes = [c for c in result[0]["indicators"]["quote"][0]["close"] if c is not None]
    if len(closes) < 2:
        raise ValueError("Yahoo Chart 데이터 부족")
    return float(closes[-2]), float(closes[-1])

def _fetch_stooq(sym):
    url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        raise ValueError(f"Stooq 응답 이상: {text[:150]!r}")
    if len(rows) < 2:
        raise ValueError(f"Stooq 데이터 부족: {text[:150]!r}")
    return float(rows[-2]["Close"]), float(rows[-1]["Close"])

def _fetch_yfinance(sym):
    ticker = yf.Ticker(sym, session=_YF_SESSION)
    hist = ticker.history(period="5d")
    if len(hist) < 2:
        raise ValueError("yfinance 데이터 부족")
    return float(hist["Close"].iloc[-2]), float(hist["Close"].iloc[-1])

def get_prices():
    data = []
    for sym, name in PORTFOLIO.items():
        result = None
        for attempt in range(2):
            try:
                prev, curr = _fetch_naver(sym)
                result = _make_result(sym, name, prev, curr); break
            except Exception as e:
                print(f"[Naver금융 오류] {sym} (시도 {attempt+1}/2): {e}")
                if attempt == 0: time.sleep(2)
        if result is None:
            for attempt in range(2):
                try:
                    prev, curr = _fetch_stooq(sym)
                    result = _make_result(sym, name, prev, curr); break
                except Exception as e:
                    print(f"[Stooq 오류] {sym} (시도 {attempt+1}/2): {e}")
                    if attempt == 0: time.sleep(2)
        if result is None:
            for attempt in range(2):
                try:
                    prev, curr = _fetch_yahoo_chart(sym)
                    result = _make_result(sym, name, prev, curr); break
                except Exception as e:
                    print(f"[YahooChart 오류] {sym} (시도 {attempt+1}/2): {e}")
                    if attempt == 0: time.sleep(3)
        if result is None:
            for attempt in range(2):
                try:
                    prev, curr = _fetch_yfinance(sym)
                    result = _make_result(sym, name, prev, curr); break
                except Exception as e:
                    print(f"[yfinance 오류] {sym} (시도 {attempt+1}/2): {e}")
                    if attempt == 0: time.sleep(5)
        if result is None:
            result = dict(symbol=sym, name=name, price="N/A", prev="N/A",
                          chg=0, arrow="", sign="", color_hex="#888", emoji="⚪")
        data.append(result)
        time.sleep(1)
    return data


# ── 2. Google News RSS ────────────────────────────────────────
def google_news(query: str, n_today=5, n_week=10, keywords=None):
    """
    오늘/최근7일 뉴스를 dict로 분리 반환:
      {"today": [...], "week": [...]}
    슬라이싱 없이 각 버킷에 직접 분류 → 오늘 기사가 0개여도 week가 오늘 자리를 차지하지 않음
    """
    enc = urllib.parse.quote(query)
    url = (f"https://news.google.com/rss/search"
           f"?q={enc}&hl=ko&gl=KR&ceid=KR:ko")
    today_date    = NOW.date()
    week_ago_date = (NOW - timedelta(days=7)).date()

    def is_relevant(title):
        if not keywords: return True
        return any(kw.lower() in title.lower() for kw in keywords)

    today_items, week_items = [], []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries:
            if not is_relevant(e.title):
                continue
            try:
                pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)
            except Exception:
                continue
            item = {"title": e.title, "link": e.link,
                    "pub": pub.strftime("%m/%d %H:%M")}
            pub_date = pub.date()
            if pub_date == today_date and len(today_items) < n_today:
                today_items.append(item)
            elif week_ago_date <= pub_date < today_date and len(week_items) < n_week:
                week_items.append(item)
            if len(today_items) >= n_today and len(week_items) >= n_week:
                break
    except Exception as ex:
        print(f"[Google RSS 오류] {query}: {ex}")
    return {"today": today_items, "week": week_items}


# ── 3. Naver News API ─────────────────────────────────────────

# 종목별 관련 키워드 (제목에 하나라도 포함되면 통과)
NAVER_KEYWORDS = {
    "디즈니":       ["디즈니", "Disney", "DIS", "픽사", "마블", "스타워즈", "ESPN", "Hulu"],
    "애플 AAPL":    ["애플", "Apple", "AAPL", "아이폰", "iPhone", "아이패드", "iPad",
                     "맥북", "MacBook", "앱스토어", "App Store", "팀 쿡", "Tim Cook"],
    "마이크로소프트": ["마이크로소프트", "Microsoft", "MSFT", "윈도우", "Windows",
                      "코파일럿", "Copilot", "애저", "Azure", "엑스박스", "Xbox",
                      "빙", "Bing", "오피스", "Office", "서피스", "Surface",
                      "사티아", "나델라", "Nadella", "Teams"],
}

def naver_news(query: str, n_today=5, n_week=10):
    """
    오늘/최근7일 뉴스를 dict로 분리 반환: {"today": [...], "week": [...]}
    - 키워드 필터로 무관한 인기 기사 제거
    - 페이지네이션(최대 300개)으로 충분한 풀 확보
    - 7일 이전 기사가 나오면 조기 종료
    """
    if not NAVER_ID or not NAVER_SECRET:
        return {"today": [], "week": []}

    keywords = NAVER_KEYWORDS.get(query, [])

    def is_relevant(title):
        if not keywords:
            return True
        return any(kw.lower() in title.lower() for kw in keywords)

    enc           = urllib.parse.quote(query)
    today_date    = NOW.date()
    week_ago_date = (NOW - timedelta(days=7)).date()
    today_items, week_items = [], []

    for start in range(1, 301, 100):   # 1, 101, 201 → 최대 300개
        url = (f"https://openapi.naver.com/v1/search/news.json"
               f"?query={enc}&display=100&start={start}&sort=date")
        req = urllib.request.Request(url)
        req.add_header("X-Naver-Client-Id", NAVER_ID)
        req.add_header("X-Naver-Client-Secret", NAVER_SECRET)

        try:
            with urllib.request.urlopen(req) as r:
                raw = json.loads(r.read().decode())
        except Exception as ex:
            print(f"[Naver 오류] {query} (start={start}): {ex}")
            break

        page_items = raw.get("items", [])
        if not page_items:
            break

        all_too_old = True   # 이 페이지 기사가 전부 7일 이전이면 중단
        for it in page_items:
            try:
                pub_dt = datetime.strptime(
                    it.get("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z"
                ).astimezone(KST)
            except Exception:
                continue

            pub_date = pub_dt.date()
            if pub_date >= week_ago_date:
                all_too_old = False   # 아직 범위 안에 있는 기사 존재

            raw_title = it["title"].replace("<b>", "").replace("</b>", "")

            if not is_relevant(raw_title):
                continue   # 무관한 기사 제거

            item = {"title": raw_title,
                    "link":  it.get("originallink") or it["link"],
                    "pub":   pub_dt.strftime("%m/%d %H:%M")}

            if pub_date == today_date and len(today_items) < n_today:
                today_items.append(item)
            elif week_ago_date <= pub_date < today_date and len(week_items) < n_week:
                week_items.append(item)

            if len(today_items) >= n_today and len(week_items) >= n_week:
                return {"today": today_items, "week": week_items}

        if all_too_old:
            break   # 7일 범위를 완전히 벗어났으므로 추가 페이지 불필요

    return {"today": today_items, "week": week_items}


# ── 4. 뉴스 수집 ─────────────────────────────────────────────
NEWS_QUERIES = {
    "DIS":  (
        "디즈니 OR Disney OR DIS stock",
        "디즈니",
        ["디즈니", "Disney", "DIS"],
    ),
    "AAPL": (
        "AAPL Apple 애플 주식",
        "애플 AAPL",
        ["애플", "Apple", "AAPL", "아이폰", "iPhone"],
    ),
    "MSFT": (
        "마이크로소프트 OR Microsoft MSFT stock",
        "마이크로소프트",
        ["마이크로소프트", "Microsoft", "MSFT"],
    ),
}

def collect_news():
    g, n = {}, {}
    for sym, (q_google, q_naver, g_keywords) in NEWS_QUERIES.items():
        g[sym] = google_news(q_google, keywords=g_keywords)   # {"today":[], "week":[]}
        n[sym] = naver_news(q_naver)                          # {"today":[], "week":[]} — 필터 없음
    return g, n


# ── 5. HTML 이메일 ────────────────────────────────────────────
def build_html(prices, g_news, n_news):
    date_str = NOW.strftime("%Y년 %m월 %d일 (%a)")

    price_rows = ""
    for s in prices:
        price_rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:10px 14px"><a href="https://m.stock.naver.com/worldstock/stock/{s['symbol']}/total" style="color:#1a237e;text-decoration:none;font-weight:500">{s['name']}</a> <span style="color:#999;font-size:12px">({s['symbol']})</span></td>
          <td style="padding:10px 14px;text-align:center;font-weight:600">${s['price']}</td>
          <td style="padding:10px 14px;text-align:center;color:#888;font-size:13px">${s['prev']}</td>
          <td style="padding:10px 14px;text-align:center;color:{s['color_hex']};font-weight:700">{s['arrow']} {s['sign']}{s['chg']}%</td>
        </tr>"""

    news_blocks = ""
    accent = {"DIS":"#0277bd","AAPL":"#c62828","MSFT":"#2e7d32"}

    def render_news_list(items):
        return "".join(
            f'<li style="margin:4px 0"><a href="{x["link"]}" style="color:#1565c0;text-decoration:none">{x["title"]}</a> '
            f'<span style="color:#aaa;font-size:11px">{x["pub"]}</span></li>'
            for x in items
        ) or "<li style='color:#aaa'>뉴스 없음</li>"

    for s in prices:
        sym = s['symbol']
        col = accent.get(sym, "#555")
        g_today = g_news.get(sym, {}).get("today", [])
        g_week  = g_news.get(sym, {}).get("week",  [])
        n_today = n_news.get(sym, {}).get("today", [])
        n_week  = n_news.get(sym, {}).get("week",  [])

        news_blocks += f"""
        <div style="margin-bottom:14px;padding:14px;background:#fff;border-left:4px solid {col};border-radius:4px">
          <p style="margin:0 0 8px;font-weight:600;font-size:14px">{s['emoji']} {s['name']} ({sym})</p>

          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">해외 뉴스 (Google News) — 오늘</p>
          <ul style="margin:0 0 8px;padding-left:18px;font-size:13px;line-height:1.6">{render_news_list(g_today)}</ul>
          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">해외 뉴스 (Google News) — 최근 7일</p>
          <ul style="margin:0 0 10px;padding-left:18px;font-size:13px;line-height:1.6">{render_news_list(g_week)}</ul>

          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">국내 뉴스 (Naver) — 오늘</p>
          <ul style="margin:0 0 8px;padding-left:18px;font-size:13px;line-height:1.6">{render_news_list(n_today)}</ul>
          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">국내 뉴스 (Naver) — 최근 7일</p>
          <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6">{render_news_list(n_week)}</ul>
        </div>"""

    best  = max(prices, key=lambda x: x['chg'])
    worst = min(prices, key=lambda x: x['chg'])
    summary = (f"{worst['name']} {worst['arrow']}{worst['chg']}% 주의, "
               f"{best['name']}  {best['arrow']}{best['sign']}{best['chg']}% 상대 강세")

    action_rows = ""
    for s in prices:
        if s['chg'] <= -3:
            action, reason, bg = "모니터링 강화", "낙폭 과대, 단기 변동성 주의", "#fff3e0"
        elif s['chg'] <= 0:
            action, reason, bg = "홀드", "소폭 하락, 관망", "#fafafa"
        else:
            action, reason, bg = "홀드 유지", "상승세, 중장기 펀더멘털 확인", "#f1f8e9"
        action_rows += f"""
        <tr style="background:{bg}">
          <td style="padding:8px 14px">{s['emoji']} {s['name']}</td>
          <td style="padding:8px 14px;font-weight:600">{action}</td>
          <td style="padding:8px 14px;color:#555;font-size:13px">{reason}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;max-width:640px">

  <tr><td style="background:#0d1b2a;padding:24px 28px">
    <p style="margin:0;color:#fff;font-size:20px;font-weight:700">📈 포트폴리오 일일 브리핑</p>
    <p style="margin:6px 0 0;color:#90a4ae;font-size:13px">{date_str} | KST {NOW.strftime('%H:%M')} 기준</p>
  </td></tr>

  <tr><td style="padding:22px 28px 0">
    <p style="margin:0 0 12px;font-size:15px;font-weight:700;color:#1a237e">💰 가격 요약</p>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;border:1px solid #e8e8e8;border-radius:8px;overflow:hidden;font-size:14px">
      <tr style="background:#0d1b2a;color:#fff">
        <th style="padding:10px 14px;text-align:left;font-weight:500">종목</th>
        <th style="padding:10px 14px;font-weight:500">현재가</th>
        <th style="padding:10px 14px;font-weight:500">전일 종가</th>
        <th style="padding:10px 14px;font-weight:500">변동률</th>
      </tr>
      {price_rows}
    </table>
  </td></tr>

  <tr><td style="padding:22px 28px 0">
    <p style="margin:0 0 12px;font-size:15px;font-weight:700;color:#1a237e">📰 종목별 뉴스</p>
    {news_blocks}
  </td></tr>

  <tr><td style="padding:16px 28px 0">
    <div style="background:#fff8e1;border-radius:8px;padding:14px 16px">
      <p style="margin:0 0 4px;font-size:13px;font-weight:700;color:#e65100">🎯 오늘의 핵심 한 줄</p>
      <p style="margin:0;font-size:13px;color:#333">{summary}</p>
    </div>
  </td></tr>

  <tr><td style="padding:16px 28px 0">
    <p style="margin:0 0 10px;font-size:15px;font-weight:700;color:#1a237e">⚡ 오늘의 액션</p>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;border:1px solid #e8e8e8;border-radius:8px;overflow:hidden;font-size:13px">
      <tr style="background:#eceff1;color:#455a64">
        <th style="padding:8px 14px;text-align:left;font-weight:600">종목</th>
        <th style="padding:8px 14px;text-align:left;font-weight:600">액션</th>
        <th style="padding:8px 14px;text-align:left;font-weight:600">근거</th>
      </tr>
      {action_rows}
    </table>
  </td></tr>

  <tr><td style="padding:20px 28px">
    <p style="margin:0;font-size:11px;color:#aaa;text-align:center">
      ⚠️ 본 브리핑은 투자 참고용이며 투자 권유가 아닙니다 | Claude AI + GitHub Actions 자동 생성
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


# ── 6. 마크다운 저장 ─────────────────────────────────────────
def save_markdown(prices, g_news, n_news):
    lines = [
        f"# 📈 포트폴리오 일일 브리핑\n",
        f"**날짜:** {NOW.strftime('%Y-%m-%d')} | **기준:** KST {NOW.strftime('%H:%M')}\n",
        "---\n",
        "## 💰 가격 요약\n",
        "| 종목 | 심볼 | 현재가 | 전일종가 | 변동률 |",
        "|------|------|--------|----------|--------|",
    ]
    for s in prices:
        lines.append(f"| {s['name']} | {s['symbol']} | ${s['price']} | ${s['prev']} | **{s['arrow']}{s['sign']}{s['chg']}%** |")
    lines.append("\n---\n\n## 📰 종목별 뉴스\n")
    for s in prices:
        sym = s['symbol']
        g_today = g_news.get(sym, {}).get("today", [])
        g_week  = g_news.get(sym, {}).get("week",  [])
        n_today = n_news.get(sym, {}).get("today", [])
        n_week  = n_news.get(sym, {}).get("week",  [])

        lines.append(f"### {s['emoji']} {s['name']} ({sym})\n")
        lines.append("**해외 뉴스 (Google) — 오늘**")
        for item in g_today:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("\n**해외 뉴스 (Google) — 최근 7일**")
        for item in g_week:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("\n**국내 뉴스 (Naver) — 오늘**")
        for item in n_today:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("\n**국내 뉴스 (Naver) — 최근 7일**")
        for item in n_week:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("")
    path = f"briefing_{NOW.strftime('%Y%m%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[저장] {path}")
    return path


# ── 7. Gmail SMTP 발송 ────────────────────────────────────────
def send_email(prices, g_news, n_news):
    worst = min(prices, key=lambda x: x['chg'])
    best  = max(prices, key=lambda x: x['chg'])
    subject = (f"📈 [주식브리핑] {NOW.strftime('%Y-%m-%d')} | "
               f"{worst['name']} {worst['arrow']}{worst['chg']}% · "
               f"{best['name']} {best['arrow']}{best['sign']}{best['chg']}%")
    plain = (f"[주식브리핑 {NOW.strftime('%m/%d')}]\n"
             + "  ".join(f"{s['name']} {s['arrow']}{s['sign']}{s['chg']}%" for s in prices)
             + "\n⚠️ 투자 참고용")
    html = build_html(prices, g_news, n_news)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))
    recipients = [r.strip() for r in RECIPIENT_EMAIL.split(",")]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER_EMAIL, SENDER_PASSWORD)
        s.sendmail(SENDER_EMAIL, recipients, msg.as_string())
    print(f"[발송 완료] {subject}")


# ── 메인 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"=== 브리핑 시작 [{NOW.strftime('%Y-%m-%d %H:%M')} KST] ===")
    prices         = get_prices()
    g_news, n_news = collect_news()
    save_markdown(prices, g_news, n_news)
    send_email(prices, g_news, n_news)
    print("=== 완료 ===")