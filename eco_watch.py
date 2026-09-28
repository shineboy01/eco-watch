"""
생태탐방원 빈자리 알림 (한려해상 · 계룡산)
- 금·토 밤, 그리고 다음 날이 공휴일인 밤(연휴)을 감시합니다.
- 새로 빈자리가 생기면 텔레그램으로 알려줍니다. (자동 예약은 하지 않음)
- 파이썬 표준 라이브러리만 사용합니다.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# ───────────── 설정 ─────────────
CENTERS = {
    "한려해상": "B024002",
    "계룡산": "B163001",
}
DAYS_AHEAD = 60          # 오늘부터 며칠 뒤까지 볼지
REQUEST_GAP_SEC = 1.5    # 요청 사이 간격 (사이트 부담 줄이기)

# 공휴일 (필요하면 추가/수정하세요)
HOLIDAYS = {
    "2026-10-03", "2026-10-05", "2026-10-09", "2026-12-25",
    "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09",
    "2027-03-01", "2027-05-05",
}
# ────────────────────────────────

API_URL = "https://res.knps.or.kr/eco/getEcoLivingRoomInfo.do"
BOOK_URL = "https://res.knps.or.kr/eco/searchEcoReservation.do?deptId={}"
STATE_FILE = Path(__file__).with_name("state.json")
WEEKDAYS = "월화수목금토일"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal vacancy alert)",
    "Referer": "https://res.knps.or.kr/eco/searchEcoReservation.do",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
}


def is_day_off(d: date) -> bool:
    return d.weekday() >= 5 or d.isoformat() in HOLIDAYS


def target_nights() -> list[date]:
    """다음 날이 쉬는 날인 밤 = 금·토 밤 + 연휴 전날/연휴 중 밤"""
    today = date.today()
    nights = []
    for i in range(DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        if is_day_off(d + timedelta(days=1)):
            nights.append(d)
    return nights


def fetch_rooms(dept_id: str, night: date) -> list[dict]:
    payload = urllib.parse.urlencode({
        "deptId": dept_id,
        "useBgnDt": night.isoformat(),
        "useEndDt": (night + timedelta(days=1)).isoformat(),
        "hrkPrdCtgId": "06001",  # 생활관(객실)
    }).encode()
    req = urllib.request.Request(API_URL, data=payload, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    rooms = []
    for g in data.get("insttGoodsInfo", []):
        remaining = int(g.get("maxNopCnt") or 0) - int(g.get("rsrvtCnt") or 0)
        ok = remaining > 0 and g.get("rsvtPsblYn") == "Y" and g.get("prdSalStcd") == "N"
        if ok:
            rooms.append({
                "id": str(g.get("prdId", "")),
                "name": str(g.get("prdNm", "")).strip(),
                "capacity": int(g.get("mmbMaxRqnpCnt") or 0),
                "price": int(g.get("salAmt") or 0),
            })
    return rooms


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[알림 미설정]\n" + text)
        return
    # 텔레그램은 한 메시지 4096자 제한 → 나눠서 보냄
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3500:
            chunks.append(cur)
            cur = ""
        cur = (cur + "\n\n" + block) if cur else block
    if cur:
        chunks.append(cur)

    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            chunk = f"({i}/{len(chunks)})\n" + chunk
        body = json.dumps({"chat_id": chat_id.strip(), "text": chunk,
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token.strip()}/sendMessage",
            data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as e:
            print(f"[텔레그램 오류] {e.code}: {e.read().decode('utf-8', 'ignore')}")
            raise
        time.sleep(1)


def main() -> None:
    old = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    now_available = set()
    new_lines = []
    nights = target_nights()

    for center, dept_id in CENTERS.items():
        for night in nights:
            try:
                rooms = fetch_rooms(dept_id, night)
            except Exception as e:
                print(f"[조회 실패] {center} {night}: {e}")
                # 실패한 날은 이전 상태 유지 (오알림 방지)
                now_available |= {k for k in old if k.startswith(f"{dept_id}|{night}|")}
                continue
            finally:
                time.sleep(REQUEST_GAP_SEC)

            for r in rooms:
                key = f"{dept_id}|{night}|{r['id']}"
                now_available.add(key)
                if key not in old:
                    tag = " 🎌연휴" if night.isoformat() in HOLIDAYS or \
                        (night + timedelta(days=1)).isoformat() in HOLIDAYS else ""
                    new_lines.append(
                        f"🏡 {center} | {night:%m/%d}({WEEKDAYS[night.weekday()]}) 1박{tag}\n"
                        f"   {r['name']} · {r['capacity']}인 · {r['price']:,}원\n"
                        f"   👉 {BOOK_URL.format(dept_id)}")

    if new_lines:
        send_telegram("🔔 생태탐방원 빈자리 발생!\n\n" + "\n\n".join(new_lines))
    print(f"확인 {len(nights)}박 × {len(CENTERS)}곳, 신규 {len(new_lines)}건")
    STATE_FILE.write_text(json.dumps(sorted(now_available), ensure_ascii=False))


if __name__ == "__main__":
    main()
