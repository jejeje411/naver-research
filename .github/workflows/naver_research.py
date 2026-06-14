#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naver_research.py — 네이버 증권 종목분석 리포트 한눈에 보기 (상승여력 + 종목별 묶음)

리포트 목록을 수집하고, 각 리포트 상세에서 투자의견·목표주가를 추출한 뒤
종목별 현재가를 가져와 상승여력(%)을 계산합니다. 같은 종목의 여러 리포트는
하나의 카드로 묶어, 의견 분포와 목표주가 컨센서스를 보여줍니다.

사용법:
    python naver_research.py                # 최근 2페이지(약 60건) 수집
    python naver_research.py --pages 5     # 5페이지 수집
    python naver_research.py --no-price    # 현재가 조회 생략(빠름)
    python naver_research.py --demo        # 네트워크 없이 데모 데이터로 미리보기

생성 결과: research_dashboard.html (브라우저로 열기)
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
from urllib.parse import urljoin, parse_qs, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("필요한 패키지가 없습니다. 먼저 실행하세요:  pip install requests beautifulsoup4")

BASE = "https://finance.naver.com"
LIST_URL = BASE + "/research/company_list.naver?&page={page}"
PRICE_URL = "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{code}"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": BASE + "/research/company_list.naver",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ---------------------------------------------------------------- 의견/목표가 추출

OPINION_PATTERNS = [
    (r"적극\s*매수|strong\s*buy", "BUY"),
    (r"투자의견[^가-힣A-Za-z0-9]{0,6}(매수|buy|outperform|overweight|비중\s*확대)", "BUY"),
    (r"투자의견[^가-힣A-Za-z0-9]{0,6}(중립|hold|neutral|market\s*perform|시장수익률)", "HOLD"),
    (r"투자의견[^가-힣A-Za-z0-9]{0,6}(매도|sell|underperform|underweight|비중\s*축소)", "SELL"),
    (r"\b(buy|매수)\s*(의견|유지|상향|제시|커버리지)", "BUY"),
    (r"\b(hold|중립)\s*(의견|유지|하향|제시)", "HOLD"),
    (r"\b(sell|매도)\s*(의견|유지|제시)", "SELL"),
    (r"trading\s*buy", "BUY"),
    (r"\bnot\s*rated\b|\bN/?R\b|투자의견\s*없음", "NA"),
]
OPINION_LABEL = {"BUY": "매수", "HOLD": "중립", "SELL": "매도", "NA": "의견없음"}

TP_PATTERN = re.compile(
    r"(?:목표\s*주가|목표가|적정\s*주가|적정가|TP)\s*(?:를|은|는|:)?\s*"
    r"([0-9][0-9,\.]*)\s*(만)?\s*원", re.IGNORECASE)


def extract_opinion(text: str) -> str:
    t = text or ""
    for pat, cls in OPINION_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            return cls
    return "NA"


def extract_target_price(text: str):
    m = TP_PATTERN.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return None
    if m.group(2):
        val *= 10000
    val = int(val)
    return val if val >= 100 else None


# ---------------------------------------------------------------- 수집

def fetch(session, url):
    r = session.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    r.encoding = "euc-kr"
    return r.text


def fetch_price(session, code):
    """종목 현재가(원). 시세 JSON 우선, 실패 시 종목 메인 페이지 스크랩."""
    if not code:
        return None
    try:
        r = session.get(PRICE_URL.format(code=code), headers=HEADERS, timeout=8)
        r.raise_for_status()
        for area in r.json().get("result", {}).get("areas", []):
            for d in area.get("datas", []):
                if d.get("nv"):
                    return int(d["nv"])
    except Exception:
        pass
    try:
        soup = BeautifulSoup(fetch(session, f"{BASE}/item/main.naver?code={code}"), "html.parser")
        node = soup.select_one("p.no_today .blind")
        if node:
            return int(re.sub(r"[^\d]", "", node.get_text()))
    except Exception:
        pass
    return None


def parse_list_page(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.select_one("table.type_1") or soup.find("table")
    rows = []
    if not table:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        stock_a = tds[0].find("a")
        title_a = tds[1].find("a") if len(tds) > 1 else None
        if not stock_a or not title_a:
            continue
        href = title_a.get("href", "")
        code = (parse_qs(urlparse(stock_a.get("href", "")).query).get("code") or [None])[0]
        pdf_a = None
        for td in tds:
            a = td.find("a", href=re.compile(r"\.pdf", re.I))
            if a:
                pdf_a = a
                break
        date = ""
        for td in reversed(tds):
            txt = td.get_text(strip=True)
            if re.match(r"\d{2}\.\d{2}\.\d{2}", txt):
                date = txt
                break
        rows.append({
            "stock": stock_a.get_text(strip=True),
            "code": code,
            "title": title_a.get_text(strip=True),
            "url": urljoin(BASE + "/research/", href),
            "broker": tds[2].get_text(strip=True) if len(tds) > 2 else "",
            "pdf": pdf_a.get("href") if pdf_a else None,
            "date": date,
        })
    return rows


def parse_detail_page(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    node = soup.select_one("td.view_cnt") or soup.select_one("div.view_cnt")
    if node is None:
        best, best_len = None, 0
        for td in soup.find_all("td"):
            txt = td.get_text(" ", strip=True)
            if len(txt) > best_len:
                best, best_len = td, len(txt)
        node = best
    if node is None:
        return ""
    return re.sub(r"\n{2,}", "\n", node.get_text("\n", strip=True))


def collect(pages: int, delay: float = 0.4, with_price: bool = True):
    session = requests.Session()
    reports = []
    for p in range(1, pages + 1):
        print(f"[목록] {p}/{pages} 페이지 수집 중...")
        try:
            rows = parse_list_page(fetch(session, LIST_URL.format(page=p)))
        except Exception as e:
            print(f"  ! 목록 페이지 실패: {e}")
            continue
        print(f"  - 리포트 {len(rows)}건 발견")
        for i, row in enumerate(rows, 1):
            summary = ""
            if row["url"]:
                try:
                    summary = parse_detail_page(fetch(session, row["url"]))
                except Exception as e:
                    print(f"  ! 상세 실패({row['stock']}): {e}")
                time.sleep(delay)
            blob = f"{row['title']}\n{summary}"
            row["opinion"] = extract_opinion(blob)
            row["target_price"] = extract_target_price(blob)
            row["summary"] = summary[:600]
            row["current_price"] = None
            reports.append(row)

    if with_price:
        codes = {r["code"] for r in reports if r["code"]}
        print(f"[시세] {len(codes)}개 종목 현재가 조회 중...")
        price_cache = {}
        for code in codes:
            price_cache[code] = fetch_price(session, code)
            time.sleep(delay)
        for r in reports:
            r["current_price"] = price_cache.get(r["code"])

    # 콘솔 요약
    for r in reports:
        tp = format(r["target_price"], ",") + "원" if r["target_price"] else "-"
        cp = format(r["current_price"], ",") + "원" if r["current_price"] else "-"
        print(f"  {r['stock']:<12} {OPINION_LABEL[r['opinion']]:<4} 목표 {tp:<11} 현재 {cp}")
    return reports


# ---------------------------------------------------------------- 데모 데이터

def demo_data():
    today = dt.date.today()
    d = lambda n: (today - dt.timedelta(days=n)).strftime("%y.%m.%d")
    # (stock, code, current, [(title, broker, opinion, tp, summary, day), ...])
    raw = [
        ("삼성전자", "005930", 71900, [
            ("메모리 업사이클 초입, 비중 확대 유효", "한국투자증권", "BUY", 98000,
             "투자의견 매수, 목표주가 98,000원 유지. HBM3E 양산 수율 개선으로 하반기 실적 가시성 확대.", 0),
            ("DRAM 가격 반등이 실적을 끌어올린다", "NH투자증권", "BUY", 95000,
             "투자의견 Buy, 목표주가 95,000원. 2분기 DRAM 가격 반등 폭 예상 상회. 파운드리 적자 축소.", 1),
            ("파운드리 회복은 좀 더 지켜봐야", "삼성증권", "HOLD", 90000,
             "투자의견 중립, 목표주가 90,000원. 메모리는 좋으나 비메모리 회복 지연 리스크 잔존.", 2),
        ]),
        ("SK하이닉스", "000660", 195000, [
            ("HBM 독주 체제 굳히기", "대신증권", "BUY", 260000,
             "투자의견 매수, 목표주가 260,000원 상향. HBM4 조기 양산 로드맵 확정.", 0),
            ("선단 공정 수급 타이트 지속", "키움증권", "BUY", 240000,
             "투자의견 Buy 유지, 목표주가 240,000원. 캐파 증설에도 수급 타이트 전망.", 1),
        ]),
        ("카카오", "035720", 48000, [
            ("비용 효율화는 진행형, 성장은 아직", "미래에셋증권", "HOLD", 52000,
             "투자의견 중립 유지, 목표주가 52,000원. 톡비즈 성장률 한 자릿수로 둔화.", 0),
            ("AI 수익화 시점 불확실", "하나증권", "SELL", 45000,
             "투자의견 매도 제시, 목표주가 45,000원. 신사업 수익화 가시성 낮고 밸류 부담.", 2),
        ]),
        ("현대차", "005380", 245000, [
            ("주주환원 확대가 리레이팅의 시작", "NH투자증권", "BUY", 320000,
             "투자의견 Buy 유지, 목표주가 320,000원 상향. 자사주 매입·소각 규모 확대.", 0),
        ]),
        ("LG에너지솔루션", "373220", 360000, [
            ("수요 둔화 구간, 눈높이 조정", "미래에셋증권", "HOLD", 380000,
             "투자의견 중립으로 하향, 목표주가 380,000원. 북미 EV 수요 둔화로 가동률 회복 지연.", 1),
        ]),
        ("POSCO홀딩스", "005490", 310000, [
            ("철강 시황 부진 장기화 우려", "키움증권", "SELL", 290000,
             "투자의견 매도 제시, 목표주가 290,000원. 중국 철강 수출 증가로 스프레드 압박 지속.", 0),
        ]),
    ]
    out = []
    for stock, code, cur, items in raw:
        for ti, (title, broker, op, tp, summ, day) in enumerate(items):
            out.append({
                "stock": stock, "code": code, "current_price": cur,
                "title": title, "broker": broker, "opinion": op,
                "target_price": tp, "summary": summ, "date": d(day),
                "url": f"https://finance.naver.com/research/company_read.naver?nid=demo{code}{ti}",
                "pdf": None,
            })
    return out


# ---------------------------------------------------------------- 대시보드 생성

def build_dashboard(reports, generated_at, demo=False):
    data_json = json.dumps(reports, ensure_ascii=False)
    demo_badge = '<span class="demo-flag">데모 데이터</span>' if demo else ""
    return DASHBOARD_TEMPLATE \
        .replace("__DATA__", data_json.replace("</", "<\\/")) \
        .replace("__GENERATED__", generated_at) \
        .replace("__DEMO__", demo_badge)


DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>리서치 한눈에 — 네이버 증권 종목 리포트</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F4F6F9; --surface:#FFFFFF; --ink:#181C22; --muted:#6E7681; --line:#E2E6EC;
  --buy:#D6273B; --sell:#1F5FD0; --hold:#7C828C; --na:#B4BAC3;
  --buy-bg:#FBEFF0; --sell-bg:#EEF3FC; --hold-bg:#F1F2F4;
  --up:#0E8A6A; --up-bg:#E6F5F0; --down:#9097A1;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);
  font-family:"IBM Plex Sans KR",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
  font-size:15px;line-height:1.55}
.mono{font-family:"IBM Plex Mono",ui-monospace,monospace}
.wrap{max-width:900px;margin:0 auto;padding:28px 20px 80px}

header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
h1{font-size:22px;font-weight:700;letter-spacing:-.02em}
.gen{color:var(--muted);font-size:12.5px}
.demo-flag{font-size:11.5px;color:var(--buy);border:1px solid var(--buy);border-radius:3px;padding:1px 6px;font-weight:500}
.src{color:var(--muted);font-size:12.5px;margin-bottom:20px}

.controls{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0 12px;border-bottom:1px solid var(--line);margin-bottom:4px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.chip{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:5px 13px;font-size:13.5px;cursor:pointer;color:var(--ink);font-weight:500}
.chip .n{color:var(--muted);font-weight:400;margin-left:4px}
.chip.on{border-color:var(--ink);background:var(--ink);color:#fff}
.chip.on .n{color:#cfd3d9}
.row2{display:flex;gap:8px;flex-wrap:wrap}
input[type=search],select{font:inherit;font-size:13.5px;padding:7px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)}
input[type=search]{flex:1;min-width:180px}
input[type=search]:focus,select:focus,.chip:focus-visible,.rep:focus-visible{outline:2px solid var(--ink);outline-offset:1px}

.group{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin-top:12px}
.ghead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.stock{font-weight:700;font-size:17px}
.code{color:var(--muted);font-size:12px}
.price{margin-left:auto;text-align:right;line-height:1.3}
.price .cur{font-size:13px;color:var(--muted)}
.price .cur b{color:var(--ink);font-weight:600}
.upside{font-size:15px;font-weight:600;display:block}
.upside.pos{color:var(--up)} .upside.neg{color:var(--down)}
.upside .cap{font-size:11px;color:var(--muted);font-weight:400;margin-right:5px}

.consensus{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px;padding-bottom:4px}
.pill{font-size:12px;font-weight:600;border-radius:999px;padding:2px 9px}
.pill.BUY{color:var(--buy);background:var(--buy-bg)} .pill.HOLD{color:var(--hold);background:var(--hold-bg)}
.pill.SELL{color:var(--sell);background:var(--sell-bg)} .pill.NA{color:var(--na);background:#F6F7F9}
.cons-tp{font-size:13px;color:var(--muted);margin-left:2px}
.cons-tp b{color:var(--ink);font-weight:600}
.bar{display:flex;height:5px;border-radius:3px;overflow:hidden;margin-top:9px;background:#EEF0F3}
.bar i{display:block} .bar .BUY{background:var(--buy)} .bar .HOLD{background:var(--hold)} .bar .SELL{background:var(--sell)} .bar .NA{background:var(--na)}

.reps{margin-top:6px;border-top:1px solid var(--line)}
.rep{display:grid;grid-template-columns:40px 1fr auto;gap:12px;align-items:baseline;padding:11px 0 11px;border-bottom:1px solid #F0F2F5;cursor:pointer}
.rep:last-child{border-bottom:none}
.rtag{font-size:11.5px;font-weight:700;text-align:center;border-radius:4px;padding:2px 0}
.rtag.BUY{color:var(--buy);background:var(--buy-bg)} .rtag.HOLD{color:var(--hold);background:var(--hold-bg)}
.rtag.SELL{color:var(--sell);background:var(--sell-bg)} .rtag.NA{color:var(--na);background:#F6F7F9}
.rmid .rtitle{font-size:13.5px;color:#2c3138;font-weight:500}
.rmid .rmeta{font-size:12px;color:var(--muted);margin-top:1px}
.rmid .rsum{font-size:12.5px;color:#555c66;margin-top:6px;display:none}
.rep.open .rsum{display:block}
.rmid .rlinks{font-size:12px;margin-top:6px;display:none;gap:12px}
.rep.open .rlinks{display:flex}
.rlinks a{color:var(--muted)}
.rtp{font-size:13px;font-weight:600;text-align:right;white-space:nowrap}
.rtp .rup{display:block;font-size:11px;font-weight:500;margin-top:1px}
.rtp .rup.pos{color:var(--up)} .rtp .rup.neg{color:var(--down)}
.empty{text-align:center;color:var(--muted);padding:60px 0}
@media (max-width:540px){
  .rep{grid-template-columns:36px 1fr}
  .rtp{grid-column:2;text-align:left;margin-top:2px}
  .price{margin-left:0;text-align:left;width:100%;margin-top:4px;display:flex;gap:14px;align-items:baseline}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>리서치 한눈에</h1>
    <span class="gen mono">__GENERATED__ 수집</span>
    __DEMO__
  </header>
  <p class="src">종목별로 묶어 의견 분포·목표주가 컨센서스·현재가 대비 상승여력을 정리했습니다. 정확한 내용은 원문 링크로 확인하세요.</p>

  <div class="controls">
    <div class="chips" id="chips" role="tablist"></div>
    <div class="row2">
      <input type="search" id="q" placeholder="종목명·제목·요약 검색">
      <select id="sort">
        <option value="upside">상승여력 높은순</option>
        <option value="count">리포트 많은순</option>
        <option value="recent">최신순</option>
        <option value="name">가나다순</option>
      </select>
    </div>
  </div>

  <div id="list"></div>
</div>

<script>
const DATA = __DATA__;
const LABEL = {ALL:"전체", BUY:"매수", HOLD:"중립", SELL:"매도", NA:"의견없음"};
const RANK = {BUY:0, HOLD:1, SELL:2, NA:3};
let opinion = "ALL", query = "", sortBy = "upside";

const $ = s => document.querySelector(s);
const esc = s => (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const won = v => v == null ? "-" : v.toLocaleString("ko-KR") + "원";

// 종목별로 묶기
function buildGroups(){
  const map = new Map();
  DATA.forEach(r => {
    const key = r.code || r.stock;
    if(!map.has(key)) map.set(key, {stock:r.stock, code:r.code, current_price:r.current_price, reps:[]});
    const g = map.get(key);
    if(r.current_price && !g.current_price) g.current_price = r.current_price;
    g.reps.push(r);
  });
  for(const g of map.values()){
    g.reps.sort((a,b) => (b.date||"").localeCompare(a.date||""));
    // 의견 분포
    g.dist = {BUY:0, HOLD:0, SELL:0, NA:0};
    g.reps.forEach(r => g.dist[r.opinion]++);
    // 컨센서스 의견 = 최빈값(동률 시 매수>중립>매도 우선)
    g.consensus = Object.keys(g.dist).sort((a,b) =>
      g.dist[b]-g.dist[a] || RANK[a]-RANK[b])[0];
    // 목표주가 컨센서스 = 평균
    const tps = g.reps.map(r => r.target_price).filter(Boolean);
    g.cons_tp = tps.length ? Math.round(tps.reduce((a,b)=>a+b,0)/tps.length) : null;
    // 상승여력
    g.upside = (g.cons_tp && g.current_price)
      ? (g.cons_tp - g.current_price) / g.current_price * 100 : null;
    g.latest = g.reps[0].date || "";
  }
  return [...map.values()];
}

function counts(groups){
  const c = {ALL: groups.length, BUY:0, HOLD:0, SELL:0, NA:0};
  groups.forEach(g => c[g.consensus]++);
  return c;
}
function renderChips(groups){
  const c = counts(groups);
  $("#chips").innerHTML = Object.keys(LABEL).map(k =>
    `<button class="chip ${opinion===k?"on":""}" data-k="${k}">${LABEL[k]}<span class="n">${c[k]||0}</span></button>`
  ).join("");
  document.querySelectorAll(".chip").forEach(b =>
    b.onclick = () => { opinion = b.dataset.k; render(); });
}
function upsideTag(u){
  if(u == null) return "";
  const cls = u >= 0 ? "pos" : "neg";
  const sign = u >= 0 ? "+" : "";
  return `<span class="upside ${cls}"><span class="cap">상승여력</span>${sign}${u.toFixed(1)}%</span>`;
}
function repUpside(g, tp){
  if(!tp || !g.current_price) return "";
  const u = (tp - g.current_price) / g.current_price * 100;
  const cls = u >= 0 ? "pos" : "neg"; const sign = u >= 0 ? "+" : "";
  return `<span class="rup ${cls} mono">${sign}${u.toFixed(0)}%</span>`;
}
function renderGroup(g){
  const dist = Object.keys(g.dist).filter(k => g.dist[k])
    .sort((a,b)=>RANK[a]-RANK[b])
    .map(k => `<span class="pill ${k}">${LABEL[k]} ${g.dist[k]}</span>`).join("");
  const total = g.reps.length;
  const bar = ["BUY","HOLD","SELL","NA"].filter(k=>g.dist[k])
    .map(k => `<i class="${k}" style="width:${g.dist[k]/total*100}%"></i>`).join("");
  const reps = g.reps.map(r => `
    <div class="rep ${r.summary?'':'nosum'}" tabindex="0">
      <span class="rtag ${r.opinion}">${LABEL[r.opinion]}</span>
      <div class="rmid">
        <div class="rtitle">${esc(r.title)}</div>
        <div class="rmeta">${esc(r.broker)} · <span class="mono">${esc(r.date)}</span></div>
        ${r.summary ? `<div class="rsum">${esc(r.summary)}</div>` : ""}
        <div class="rlinks">
          ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">원문</a>` : ""}
          ${r.pdf ? `<a href="${esc(r.pdf)}" target="_blank" rel="noopener">PDF</a>` : ""}
        </div>
      </div>
      <div class="rtp mono">${won(r.target_price)}${repUpside(g, r.target_price)}</div>
    </div>`).join("");
  return `<article class="group">
    <div class="ghead">
      <span class="stock">${esc(g.stock)}</span>
      ${g.code ? `<span class="code mono">${esc(g.code)}</span>` : ""}
      <span class="price">
        <span class="cur">현재 <b class="mono">${won(g.current_price)}</b></span>
        ${upsideTag(g.upside)}
      </span>
    </div>
    <div class="consensus">
      ${dist}
      ${g.cons_tp ? `<span class="cons-tp">목표가 컨센서스 <b class="mono">${won(g.cons_tp)}</b></span>` : ""}
    </div>
    <div class="bar">${bar}</div>
    <div class="reps">${reps}</div>
  </article>`;
}
function render(){
  const groups = buildGroups();
  renderChips(groups);
  const q = query.trim().toLowerCase();
  let rows = groups.filter(g =>
    (opinion === "ALL" || g.consensus === opinion) &&
    (!q || g.reps.some(r => [g.stock, r.title, r.summary, r.broker].join(" ").toLowerCase().includes(q)))
  );
  const cmp = {
    upside: (a,b) => (b.upside ?? -1e9) - (a.upside ?? -1e9),
    count:  (a,b) => b.reps.length - a.reps.length || (b.upside??-1e9)-(a.upside??-1e9),
    recent: (a,b) => (b.latest||"").localeCompare(a.latest||""),
    name:   (a,b) => a.stock.localeCompare(b.stock, "ko"),
  }[sortBy];
  rows.sort(cmp);
  $("#list").innerHTML = rows.length
    ? rows.map(renderGroup).join("")
    : '<p class="empty">조건에 맞는 종목이 없습니다. 필터를 바꿔보세요.</p>';
  document.querySelectorAll(".rep").forEach(el => {
    const t = () => el.classList.toggle("open");
    el.onclick = t;
    el.onkeydown = e => { if(e.key==="Enter"||e.key===" "){ e.preventDefault(); t(); } };
  });
}
$("#q").addEventListener("input", e => { query = e.target.value; render(); });
$("#sort").addEventListener("change", e => { sortBy = e.target.value; render(); });
render();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="네이버 증권 종목 리포트 대시보드 생성")
    ap.add_argument("--pages", type=int, default=2, help="수집할 목록 페이지 수 (기본 2)")
    ap.add_argument("--delay", type=float, default=0.4, help="요청 간 딜레이(초, 기본 0.4)")
    ap.add_argument("--out", default="research_dashboard.html", help="출력 파일명")
    ap.add_argument("--no-price", action="store_true", help="현재가 조회 생략")
    ap.add_argument("--demo", action="store_true", help="데모 데이터로 생성")
    args = ap.parse_args()

    if args.demo:
        reports = demo_data()
    else:
        reports = collect(args.pages, args.delay, with_price=not args.no_price)
        if not reports:
            sys.exit("수집된 리포트가 없습니다. 네트워크 또는 페이지 구조 변경 여부를 확인하세요.")

    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_dashboard(reports, generated, demo=args.demo))
    print(f"\n완료: {args.out}  (종목 {len({r['code'] or r['stock'] for r in reports})}개 / 리포트 {len(reports)}건)")


if __name__ == "__main__":
    main()
