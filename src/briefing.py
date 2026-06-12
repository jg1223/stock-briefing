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

# yfinance 차단 우회용 브라우저 헤더 세션 (백업용으로 유지)
_YF_SESSION = requests.Session()
_YF_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
})

# ── 환경변수 (GitHub Secrets 에서 주입) ──────────────────────
SENDER_EMAIL    = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]
NAVER_ID        = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_SECRET    = os.environ.get("NAVER_CLIENT_SECRET", "")

# ── 포트폴리오 ────────────────────────────────────────────────
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
    """네이버 금융 해외증시 API 에서 현재가/전일종가 가져오기 (1차 시도)"""
    url = f"https://api.stock.naver.com/stock/{sym}.O/basic"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))

    curr = data.get("closePrice") or data.get("currentPrice") or data.get("now")
    prev = data.get("previousClose") or data.get("closePriceBeforeDay")

    if curr is None or prev is None:
        raise ValueError("네이버 응답 필드 누락")

    def to_float(v):
        if isinstance(v, str):
            v = v.replace(",", "")
        return float(v)

    return to_float(prev), to_float(curr)


def _fetch_stooq(sym):
    """Stooq CSV 에서 최근 2거래일 종가 가져오기 (yfinance 대체, IP 차단 거의 없음)"""
    url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) < 2:
        raise ValueError("Stooq 데이터 부족")
    prev = float(rows[-2]["Close"])
    curr = float(rows[-1]["Close"])
    return prev, curr


def _fetch_yfinance(sym):
    """yfinance 백업 (Stooq 실패 시도)"""
    ticker = yf.Ticker(sym, session=_YF_SESSION)
    hist = ticker.history(period="5d")
    if len(hist) < 2:
        raise ValueError("yfinance 데이터 부족")
    prev = float(hist["Close"].iloc[-2])
    curr = float(hist["Close"].iloc[-1])
    return prev, curr


def get_prices():
    data = []
    for sym, name in PORTFOLIO.items():
        result = None

        # 1차: 네이버 금융 시도 (최대 2회)
        for attempt in range(2):
            try:
                prev, curr = _fetch_naver(sym)
                result = _make_result(sym, name, prev, curr)
                break
            except Exception as e:
                print(f"[Naver금융 오류] {sym} (시도 {attempt+1}/2): {e}")
                if attempt == 0:
                    time.sleep(2)

        # 2차: Stooq 폴백 (최대 2회)
        if result is None:
            for attempt in range(2):
                try:
                    prev, curr = _fetch_stooq(sym)
                    result = _make_result(sym, name, prev, curr)
                    break
                except Exception as e:
                    print(f"[Stooq 오류] {sym} (시도 {attempt+1}/2): {e}")
                    if attempt == 0:
                        time.sleep(2)

        # 3차: yfinance 최종 백업 (최대 2회)
        if result is None:
            for attempt in range(2):
                try:
                    prev, curr = _fetch_yfinance(sym)
                    result = _make_result(sym, name, prev, curr)
                    break
                except Exception as e:
                    print(f"[yfinance 오류] {sym} (시도 {attempt+1}/2): {e}")
                    if attempt == 0:
                        time.sleep(5)

        if result is None:
            result = dict(symbol=sym, name=name, price="N/A", prev="N/A",
                           chg=0, arrow="", sign="", color_hex="#888",
                           emoji="⚪")
        data.append(result)

        time.sleep(1)  # 종목 간 간격
    return data


# ── 2. Google News RSS ────────────────────────────────────────
def google_news(query: str, n_today=5, n_yesterday=5):
    """오늘 뉴스 n_today개 + 어제 뉴스 n_yesterday개 반환"""
    enc = urllib.parse.quote(query)
    url = (f"https://news.google.com/rss/search"
           f"?q={enc}&hl=ko&gl=KR&ceid=KR:ko")
    today_date     = NOW.date()
    yesterday_date = (NOW - timedelta(days=1)).date()

    today_items, yesterday_items = [], []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries:
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)
            item = {"title": e.title, "link": e.link,
                    "pub": pub.strftime("%m/%d %H:%M")}
            if pub.date() == today_date and len(today_items) < n_today:
                today_items.append(item)
            elif pub.date() == yesterday_date and len(yesterday_items) < n_yesterday:
                yesterday_items.append(item)

            if len(today_items) >= n_today and len(yesterday_items) >= n_yesterday:
                break
        return today_items + yesterday_items
    except Exception as ex:
        print(f"[Google RSS 오류] {query}: {ex}")
        return today_items + yesterday_items


# ── 3. Naver News API ─────────────────────────────────────────
def naver_news(query: str, n_today=5, n_yesterday=5):
    """오늘 뉴스 n_today개 + 어제 뉴스 n_yesterday개 반환"""
    if not NAVER_ID or not NAVER_SECRET:
        return []
    enc = urllib.parse.quote(query)
    # 여유있게 더 많이 받아와서 날짜별로 분류 (display 최대 100)
    display = max(n_today, n_yesterday) * 6
    url = (f"https://openapi.naver.com/v1/search/news.json"
           f"?query={enc}&display={display}&sort=date")
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_SECRET)

    today_date     = NOW.date()
    yesterday_date = (NOW - timedelta(days=1)).date()
    today_items, yesterday_items = [], []

    try:
        with urllib.request.urlopen(req) as r:
            raw = json.loads(r.read().decode())
        for it in raw.get("items", []):
            try:
                pub_dt = datetime.strptime(it.get("pubDate",""), "%a, %d %b %Y %H:%M:%S %z").astimezone(KST)
            except Exception:
                continue
            item = {"title": it["title"].replace("<b>","").replace("</b>",""),
                    "link":  it.get("originallink") or it["link"],
                    "pub":   pub_dt.strftime("%m/%d %H:%M")}
            if pub_dt.date() == today_date and len(today_items) < n_today:
                today_items.append(item)
            elif pub_dt.date() == yesterday_date and len(yesterday_items) < n_yesterday:
                yesterday_items.append(item)

            if len(today_items) >= n_today and len(yesterday_items) >= n_yesterday:
                break
        return today_items + yesterday_items
    except Exception as ex:
        print(f"[Naver 오류] {query}: {ex}")
        return today_items + yesterday_items


# ── 4. 뉴스 수집 ─────────────────────────────────────────────
NEWS_QUERIES = {
    "DIS":  ("디즈니 주식",          "디즈니 주식"),
    "AAPL": ("애플 주식",            "애플 주식"),
    "MSFT": ("마이크로소프트 주식",   "마이크로소프트 주식"),
}

def collect_news():
    g, n = {}, {}
    for sym, (q_en, q_ko) in NEWS_QUERIES.items():
        g[sym] = google_news(q_en)
        n[sym] = naver_news(q_ko)
    return g, n


# ── 5. HTML 이메일 ────────────────────────────────────────────
def build_html(prices, g_news, n_news):
    date_str = NOW.strftime("%Y년 %m월 %d일 (%a)")

    # 가격 행
    price_rows = ""
    for s in prices:
        price_rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:10px 14px">{s['name']} <span style="color:#999;font-size:12px">({s['symbol']})</span></td>
          <td style="padding:10px 14px;text-align:center;font-weight:600">${s['price']}</td>
          <td style="padding:10px 14px;text-align:center;color:#888;font-size:13px">${s['prev']}</td>
          <td style="padding:10px 14px;text-align:center;color:{s['color_hex']};font-weight:700">{s['arrow']} {s['sign']}{s['chg']}%</td>
        </tr>"""

    # 뉴스 블록 (오늘/어제 섹션 구분, 각 최대 5개)
    news_blocks = ""
    accent = {"DIS":"#0277bd","AAPL":"#c62828","MSFT":"#2e7d32"}

    def render_news_list(items):
        return "".join(
            f'<li style="margin:4px 0"><a href="{x["link"]}" style="color:#1565c0;text-decoration:none">{x["title"]}</a> '
            f'<span style="color:#aaa;font-size:11px">{x["pub"]}</span></li>'
            for x in items
        ) or "<li style='color:#aaa'>뉴스 없음</li>"

    for s in prices:
        sym  = s['symbol']
        col  = accent.get(sym, "#555")
        gn   = g_news.get(sym, [])
        nn   = n_news.get(sym, [])

        # 함수에서 [오늘 5개] + [어제 5개] 순서로 반환됨
        g_today, g_yesterday = gn[:5], gn[5:10]
        n_today, n_yesterday = nn[:5], nn[5:10]

        g_today_li     = render_news_list(g_today)
        g_yesterday_li = render_news_list(g_yesterday)
        n_today_li     = render_news_list(n_today)
        n_yesterday_li = render_news_list(n_yesterday)

        news_blocks += f"""
        <div style="margin-bottom:14px;padding:14px;background:#fff;border-left:4px solid {col};border-radius:4px">
          <p style="margin:0 0 8px;font-weight:600;font-size:14px">{s['emoji']} {s['name']} ({sym})</p>

          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">해외 뉴스 (Google News) — 오늘</p>
          <ul style="margin:0 0 8px;padding-left:18px;font-size:13px;line-height:1.6">{g_today_li}</ul>
          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">해외 뉴스 (Google News) — 어제</p>
          <ul style="margin:0 0 10px;padding-left:18px;font-size:13px;line-height:1.6">{g_yesterday_li}</ul>

          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">국내 뉴스 (Naver) — 오늘</p>
          <ul style="margin:0 0 8px;padding-left:18px;font-size:13px;line-height:1.6">{n_today_li}</ul>
          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">국내 뉴스 (Naver) — 어제</p>
          <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6">{n_yesterday_li}</ul>
        </div>"""

    # 핵심 한 줄
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

  <!-- 헤더 -->
  <tr><td style="background:#0d1b2a;padding:24px 28px">
    <p style="margin:0;color:#fff;font-size:20px;font-weight:700">📈 포트폴리오 일일 브리핑</p>
    <p style="margin:6px 0 0;color:#90a4ae;font-size:13px">{date_str} | KST {NOW.strftime('%H:%M')} 기준</p>
  </td></tr>

  <!-- 가격 요약 -->
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

  <!-- 뉴스 -->
  <tr><td style="padding:22px 28px 0">
    <p style="margin:0 0 12px;font-size:15px;font-weight:700;color:#1a237e">📰 종목별 뉴스</p>
    {news_blocks}
  </td></tr>

  <!-- 핵심 한 줄 -->
  <tr><td style="padding:16px 28px 0">
    <div style="background:#fff8e1;border-radius:8px;padding:14px 16px">
      <p style="margin:0 0 4px;font-size:13px;font-weight:700;color:#e65100">🎯 오늘의 핵심 한 줄</p>
      <p style="margin:0;font-size:13px;color:#333">{summary}</p>
    </div>
  </td></tr>

  <!-- 액션 -->
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

  <!-- 푸터 -->
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
        gn  = g_news.get(sym, [])
        nn  = n_news.get(sym, [])
        g_today, g_yesterday = gn[:5], gn[5:10]
        n_today, n_yesterday = nn[:5], nn[5:10]

        lines.append(f"### {s['emoji']} {s['name']} ({sym})\n")

        lines.append("**해외 뉴스 (Google) — 오늘**")
        for item in g_today:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("\n**해외 뉴스 (Google) — 어제**")
        for item in g_yesterday:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")

        lines.append("\n**국내 뉴스 (Naver) — 오늘**")
        for item in n_today:
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("\n**국내 뉴스 (Naver) — 어제**")
        for item in n_yesterday:
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

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER_EMAIL, SENDER_PASSWORD)
        s.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

    print(f"[발송 완료] {subject}")


# ── 메인 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"=== 브리핑 시작 [{NOW.strftime('%Y-%m-%d %H:%M')} KST] ===")
    prices        = get_prices()
    g_news, n_news = collect_news()
    save_markdown(prices, g_news, n_news)
    send_email(prices, g_news, n_news)
    print("=== 완료 ===")