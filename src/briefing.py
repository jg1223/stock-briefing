"""
포트폴리오 일일 주식 브리핑
- yfinance 로 해외 주가 수집
- Google News RSS 로 영문 뉴스 수집
- Naver News API 로 국내 뉴스 수집
- Gmail SMTP 로 HTML 이메일 실발송
"""

import os, json, smtplib, feedparser, urllib.request, urllib.parse, yfinance as yf
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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
def get_prices():
    data = []
    for sym, name in PORTFOLIO.items():
        try:
            hist = yf.Ticker(sym).history(period="2d")
            if len(hist) < 2:
                raise ValueError("데이터 부족")
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            chg  = round((curr - prev) / prev * 100, 2)
            data.append(dict(
                symbol=sym, name=name,
                price=round(curr, 2), prev=round(prev, 2),
                chg=chg,
                arrow="▲" if chg >= 0 else "▼",
                sign="+" if chg >= 0 else "",
                color_hex="#2e7d32" if chg >= 0 else "#c62828",
                emoji="🟢" if chg >= 0 else "🔴",
            ))
        except Exception as e:
            print(f"[주가 오류] {sym}: {e}")
            data.append(dict(symbol=sym, name=name, price="N/A", prev="N/A",
                             chg=0, arrow="", sign="", color_hex="#888",
                             emoji="⚪"))
    return data


# ── 2. Google News RSS ────────────────────────────────────────
def google_news(query: str, n=3):
    enc = urllib.parse.quote(query)
    url = (f"https://news.google.com/rss/search"
           f"?q={enc}&hl=ko&gl=KR&ceid=KR:ko")
    try:
        feed = feedparser.parse(url)
        cutoff = NOW - timedelta(days=2)
        items = []
        for e in feed.entries:
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)
            if pub < cutoff:
                continue
            items.append({"title": e.title, "link": e.link,
                          "pub": pub.strftime("%m/%d %H:%M")})
            if len(items) >= n:
                break
        return items
    except Exception as ex:
        print(f"[Google RSS 오류] {query}: {ex}")
        return []


# ── 3. Naver News API ─────────────────────────────────────────
def naver_news(query: str, n=3):
    if not NAVER_ID or not NAVER_SECRET:
        return []
    enc = urllib.parse.quote(query)
    url = (f"https://openapi.naver.com/v1/search/news.json"
           f"?query={enc}&display={n}&sort=date")
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_SECRET)
    try:
        with urllib.request.urlopen(req) as r:
            raw = json.loads(r.read().decode())
        return [
            {"title": it["title"].replace("<b>","").replace("</b>",""),
             "link":  it.get("originallink") or it["link"],
             "pub":   it.get("pubDate","")[:16]}
            for it in raw.get("items", [])
        ]
    except Exception as ex:
        print(f"[Naver 오류] {query}: {ex}")
        return []


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

    # 뉴스 블록
    news_blocks = ""
    accent = {"DIS":"#0277bd","AAPL":"#c62828","MSFT":"#2e7d32"}
    for s in prices:
        sym  = s['symbol']
        col  = accent.get(sym, "#555")
        gn   = g_news.get(sym, [])
        nn   = n_news.get(sym, [])
        g_li = "".join(f'<li style="margin:4px 0"><a href="{x["link"]}" style="color:#1565c0;text-decoration:none">{x["title"]}</a> <span style="color:#aaa;font-size:11px">{x["pub"]}</span></li>' for x in gn) or "<li style='color:#aaa'>뉴스 없음</li>"
        n_li = "".join(f'<li style="margin:4px 0"><a href="{x["link"]}" style="color:#1565c0;text-decoration:none">{x["title"]}</a></li>' for x in nn) or "<li style='color:#aaa'>뉴스 없음</li>"
        news_blocks += f"""
        <div style="margin-bottom:14px;padding:14px;background:#fff;border-left:4px solid {col};border-radius:4px">
          <p style="margin:0 0 8px;font-weight:600;font-size:14px">{s['emoji']} {s['name']} ({sym})</p>
          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">해외 뉴스 (Google News)</p>
          <ul style="margin:0 0 10px;padding-left:18px;font-size:13px;line-height:1.6">{g_li}</ul>
          <p style="margin:0 0 4px;font-size:12px;color:#777;font-weight:600">국내 뉴스 (Naver)</p>
          <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.6">{n_li}</ul>
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
        lines.append(f"### {s['emoji']} {s['name']} ({sym})\n")
        for item in g_news.get(sym, []):
            lines.append(f"- [{item['title']}]({item['link']}) _{item['pub']}_")
        lines.append("")
        for item in n_news.get(sym, []):
            lines.append(f"- [{item['title']}]({item['link']})")
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