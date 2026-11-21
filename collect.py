#!/usr/bin/env python3
"""국토교통부 아파트 매매 실거래가 공개 API -> data.json (점프업 아파트용)

사용법
  MOLIT_KEY=발급받은인증키 python collect.py            # 최근 12개월 수집, data.json 생성
  MOLIT_KEY=... python collect.py --check               # 지역코드가 맞는지 확인(파일 안 만듦)
  MOLIT_KEY=... python collect.py --only 서울 --months 3 # 일부만 시험 수집

옵션
  --months N   수집 기간(개월, 기본 12, 이번 달 포함)
  --only 시도  서울/경기/인천 중 하나만
  --out 경로   저장 파일(기본 data.json)

환경변수
  MOLIT_KEY       공공데이터포털 인증키 (Encoding/Decoding 어느 쪽이든 됨)
  MOLIT_ENDPOINT  API 주소를 바꿀 때만 (기본: 아파트 매매 실거래가 상세 자료)

평형 기준
  59㎡형 = 전용 55 이상 65 미만, 84㎡형 = 전용 80 이상 90 미만
가격 기준(만원)
  최근 3개월 거래가 3건 이상이면 그 중위값, 아니면 전체 기간 중위값을 써요.
  거래 건수(n)는 그 가격에 쓰인 건수, 기준 개월(b)은 3 또는 전체 기간이에요.
  l = 가장 최근 거래 [가격, 날짜, 층]. 해제(취소)된 거래는 모두 제외해요.
"""
import argparse
import collections
import datetime
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ENDPOINT = os.environ.get(
    "MOLIT_ENDPOINT",
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
)

# (시도, 표시 이름, 법정동코드 앞 5자리). 틀린 코드가 있으면 --check 로 확인하고 여기서 고치세요.
REGIONS = [
    ("서울", "종로구", "11110"), ("서울", "중구", "11140"), ("서울", "용산구", "11170"),
    ("서울", "성동구", "11200"), ("서울", "광진구", "11215"), ("서울", "동대문구", "11230"),
    ("서울", "중랑구", "11260"), ("서울", "성북구", "11290"), ("서울", "강북구", "11305"),
    ("서울", "도봉구", "11320"), ("서울", "노원구", "11350"), ("서울", "은평구", "11380"),
    ("서울", "서대문구", "11410"), ("서울", "마포구", "11440"), ("서울", "양천구", "11470"),
    ("서울", "강서구", "11500"), ("서울", "구로구", "11530"), ("서울", "금천구", "11545"),
    ("서울", "영등포구", "11560"), ("서울", "동작구", "11590"), ("서울", "관악구", "11620"),
    ("서울", "서초구", "11650"), ("서울", "강남구", "11680"), ("서울", "송파구", "11710"),
    ("서울", "강동구", "11740"),
    ("경기", "수원시 장안구", "41111"), ("경기", "수원시 권선구", "41113"),
    ("경기", "수원시 팔달구", "41115"), ("경기", "수원시 영통구", "41117"),
    ("경기", "성남시 수정구", "41131"), ("경기", "성남시 중원구", "41133"),
    ("경기", "성남시 분당구", "41135"), ("경기", "의정부시", "41150"),
    ("경기", "안양시 만안구", "41171"), ("경기", "안양시 동안구", "41173"),
    ("경기", "부천시 원미구", "41192"), ("경기", "부천시 소사구", "41194"), ("경기", "부천시 오정구", "41196"), ("경기", "광명시", "41210"), ("경기", "평택시", "41220"),
    ("경기", "동두천시", "41250"), ("경기", "안산시 상록구", "41271"),
    ("경기", "안산시 단원구", "41273"), ("경기", "고양시 덕양구", "41281"),
    ("경기", "고양시 일산동구", "41285"), ("경기", "고양시 일산서구", "41287"),
    ("경기", "과천시", "41290"), ("경기", "구리시", "41310"), ("경기", "남양주시", "41360"),
    ("경기", "오산시", "41370"), ("경기", "시흥시", "41390"), ("경기", "군포시", "41410"),
    ("경기", "의왕시", "41430"), ("경기", "하남시", "41450"),
    ("경기", "용인시 처인구", "41461"), ("경기", "용인시 기흥구", "41463"),
    ("경기", "용인시 수지구", "41465"), ("경기", "파주시", "41480"), ("경기", "이천시", "41500"),
    ("경기", "안성시", "41550"), ("경기", "김포시", "41570"), ("경기", "화성시", ["41591", "41593", "41595", "41597"]),
    ("경기", "광주시", "41610"), ("경기", "양주시", "41630"), ("경기", "포천시", "41650"),
    ("경기", "여주시", "41670"),
    ("인천", "제물포구", "28125"), ("인천", "영종구", "28155"), ("인천", "미추홀구", "28177"),
    ("인천", "연수구", "28185"), ("인천", "남동구", "28200"), ("인천", "부평구", "28237"),
    ("인천", "계양구", "28245"), ("인천", "서해구", "28275"), ("인천", "검단구", "28290"),
]

BANDS = {"59": (55.0, 65.0), "84": (80.0, 90.0)}

# 응답 태그 이름이 버전마다 달라서 여러 이름을 같이 받는다
TAGS = {
    "name": ("aptNm", "아파트"),
    "dong": ("umdNm", "법정동"),
    "area": ("excluUseAr", "전용면적"),
    "amount": ("dealAmount", "거래금액"),
    "year": ("buildYear", "건축년도"),
    "cancel": ("cdealType", "해제여부"),
    "y": ("dealYear", "년"),
    "m": ("dealMonth", "월"),
    "d": ("dealDay", "일"),
    "floor": ("floor", "층"),
}
MIN_RECENT = 3     # 최근 구간 거래가 이 건수 이상이면 최근 구간 중위값을 가격으로 쓴다
RECENT_MONTHS = 3


def encode_key(key):
    key = key.strip()
    return key if "%" in key else urllib.parse.quote(key, safe="")


def fetch_xml(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jumpup-collector"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("네트워크 오류: %s" % last)


class QuotaError(RuntimeError):
    pass


def get(item, field):
    for tag in TAGS[field]:
        el = item.find(tag)
        if el is not None and el.text is not None:
            return el.text.strip()
    return ""


def fetch_month(code, ym, key, rows=1000):
    """한 지역·한 달의 거래 목록(딕셔너리 리스트)"""
    out, page, total = [], 1, None
    while True:
        url = "%s?serviceKey=%s&LAWD_CD=%s&DEAL_YMD=%s&pageNo=%d&numOfRows=%d" % (
            ENDPOINT, encode_key(key), code, ym, page, rows)
        text = fetch_xml(url)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise RuntimeError("XML이 아닌 응답: %s" % text[:200].replace("\n", " "))
        # 오류 응답 (인증키 문제, 일일 한도 등)
        err = root.findtext(".//returnReasonCode") or ""
        auth = root.findtext(".//returnAuthMsg") or ""
        code_el = root.findtext(".//resultCode") or ""
        msg = root.findtext(".//resultMsg") or auth
        if err or (code_el and code_el not in ("00", "000")):
            c = err or code_el
            if c in ("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"):
                raise QuotaError("일일 호출 한도 초과(%s)" % msg)
            raise RuntimeError("API 오류 %s: %s" % (c, msg))
        items = root.findall(".//item")
        for it in items:
            out.append({k: get(it, k) for k in TAGS})
        if total is None:
            try:
                total = int(root.findtext(".//totalCount") or 0)
            except ValueError:
                total = 0
        if not items or len(out) >= total:
            break
        page += 1
        time.sleep(0.1)
    return out


def fetch_region_month(codes, ym, key, rows=1000):
    """코드가 하나면 그대로, 여러 개면(화성시 구 분리) 합치되 같은 거래가 겹치면 한 번만 센다"""
    if len(codes) == 1:
        return fetch_month(codes[0], ym, key, rows)
    per, last_err = [], None
    for code in codes:
        try:
            deals = fetch_month(code, ym, key, rows)
        except QuotaError:
            raise
        except RuntimeError as e:
            last_err = e
            continue
        per.append(collections.Counter(tuple(d[k] for k in TAGS) for d in deals))
    if not per:
        raise last_err or RuntimeError("응답 없음")
    merged = collections.Counter()
    for cnt in per:
        for sig, n in cnt.items():
            merged[sig] = max(merged[sig], n)
    out = []
    for sig, n in merged.items():
        out.extend([dict(zip(TAGS, sig))] * n)
    return out


def months_back(n, today=None):
    today = today or datetime.date.today()
    y, m = today.year, today.month
    res = []
    for _ in range(n):
        res.append("%04d%02d" % (y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(res))


def to_int(s):
    s = s.replace(",", "").strip()
    return int(s) if s.isdigit() else None


def band_of(area):
    for name, (lo, hi) in BANDS.items():
        if lo <= area < hi:
            return name
    return None


def deal_date(d, ym):
    y, m, dd = to_int(d["y"]), to_int(d["m"]), to_int(d["d"])
    if not (y and m):
        y, m = int(ym[:4]), int(ym[4:])
    return "%04d-%02d-%02d" % (y, m, dd) if dd else "%04d-%02d" % (y, m)


def aggregate(sido, gu, deals, store, ym):
    """deals를 단지(동+이름) 단위로 합산해 store에 누적"""
    for d in deals:
        if d["cancel"]:  # 'O' 등 값이 있으면 해제된 거래
            continue
        amount = to_int(d["amount"])
        try:
            area = float(d["area"])
        except ValueError:
            continue
        band = band_of(area)
        if not d["name"] or amount is None or band is None:
            continue
        key = (sido, gu, d["dong"], d["name"])
        rec = store.setdefault(key, {"years": collections.Counter(), "59": [], "84": []})
        rec[band].append((amount, deal_date(d, ym), to_int(d["floor"]), ym))
        y = to_int(d["year"])
        if y:
            rec["years"][y] += 1


def build(store, recent, total_months):
    res = []
    for (sido, gu, dong, name), rec in sorted(store.items()):
        item = {"sido": sido, "gu": gu, "dong": dong, "name": name,
                "year": rec["years"].most_common(1)[0][0] if rec["years"] else None}
        for b in BANDS:
            deals = rec[b]
            near = [x for x in deals if x[3] in recent]
            if len(near) >= MIN_RECENT:
                used, basis = near, len(recent)
            else:
                used, basis = deals, total_months
            item["p" + b] = int(statistics.median(x[0] for x in used)) if used else None
            item["n" + b] = len(used)
            item["b" + b] = basis if used else None
            last = max(deals, key=lambda x: (x[1], x[0])) if deals else None
            item["l" + b] = [last[0], last[1], last[2]] if last else None
        res.append(item)
    return res


def expand(regions):
    """(시도, 이름, 코드 또는 코드목록) -> (시도, 이름, 코드) 한 줄씩"""
    for sido, gu, codes in regions:
        for c in ([codes] if isinstance(codes, str) else codes):
            yield sido, gu, c


def run_check(key, regions):
    ym = months_back(2)[0]  # 지난달 (거래 신고가 거의 다 들어온 달)
    print("지역코드 점검 (기준월 %s). 법정동 이름이 그 구의 동네가 맞는지 보세요." % ym)
    bad = 0
    for sido, gu, code in expand(regions):
        try:
            deals = fetch_month(code, ym, key, rows=100)
        except QuotaError as e:
            print("중단:", e)
            return 1
        except RuntimeError as e:
            print("%s %s (%s) 오류: %s" % (sido, gu, code, e))
            bad += 1
            continue
        top = collections.Counter(d["dong"] for d in deals if d["dong"]).most_common(4)
        flag = "" if deals else "  <- 거래 0건: 코드 확인 필요"
        bad += 0 if deals else 1
        print("%s %s (%s): %d건 | %s%s" % (sido, gu, code, len(deals), ", ".join(n for n, _ in top), flag))
    print("점검 끝. 확인이 필요한 지역: %d곳" % bad)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--only", choices=["서울", "경기", "인천"])
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    key = os.environ.get("MOLIT_KEY", "").strip()
    if not key:
        sys.exit("MOLIT_KEY 환경변수에 공공데이터포털 인증키를 넣어주세요.")
    regions = [r for r in REGIONS if not a.only or r[0] == a.only]
    if a.check:
        sys.exit(run_check(key, regions))

    yms = months_back(a.months)
    store, calls, fails = {}, 0, []
    for sido, gu, codes in regions:
        codes = [codes] if isinstance(codes, str) else list(codes)
        n_deals = 0
        for ym in yms:
            try:
                deals = fetch_region_month(codes, ym, key)
            except QuotaError as e:
                print("중단:", e, "- 지금까지 모은 데이터만 저장하지 않고 종료해요.")
                sys.exit(2)
            except RuntimeError as e:
                fails.append("%s %s %s: %s" % (sido, gu, ym, e))
                continue
            calls += 1
            n_deals += len(deals)
            aggregate(sido, gu, deals, store, ym)
            time.sleep(0.05)
        print("%s %s: %d건" % (sido, gu, n_deals), flush=True)

    complexes = build(store, set(yms[-RECENT_MONTHS:]), len(yms))
    if len(complexes) < 100:
        sys.exit("수집된 단지가 %d개뿐이에요. 인증키나 API 주소를 확인하세요. 실패 예: %s"
                 % (len(complexes), "; ".join(fails[:3]) or "없음"))
    out = {
        "generated": datetime.date.today().isoformat(),
        "period": "%s~%s" % (yms[0], yms[-1]),
        "bands": {k: "%g~%g" % v for k, v in BANDS.items()},
        "recent_months": RECENT_MONTHS,
        "source": "국토교통부 아파트 매매 실거래가 공개 API",
        "complexes": complexes,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("완료: 단지 %d개, 호출 %d회, 실패 %d건 -> %s (%.0f KB)"
          % (len(complexes), calls, len(fails), a.out, os.path.getsize(a.out) / 1024))
    for f_ in fails[:10]:
        print("실패:", f_)


if __name__ == "__main__":
    main()
