"""
spike_watch.py — 대장 관심 '끼 강한 종목' 급등 알림 (2026-11-07, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "끼가 강한 놈들 중심으로 보거든 — 수급이 들어오면 바로 치고 가는 놈들." (11-06 원익홀딩스 -9% → +6%처럼)
매수는 대장이 하고, 리나는 놓치지 않게 신호만 준다(매매 안 함). 대장이 한투 관심그룹 'isee5'(항상 5종목)에 넣은 종목만 1분마다 본다.
  A. 급등 시작형 — 음봉이든 양봉이든 5분 안에 +3%↑ 치고 올라옴 & 그 5분 거래대금이 오늘 평소(5분 평균)의 2.5배↑·3억↑
     → "여기서부터 수급 보고 매수 준비". 같은 종목 30분에 한 번.
  B. 계단식 → 돌파형 — 최근 60분 동안 15분마다 저점·고점을 높이며(4칸 중 3칸↑) +2%↑ 야금야금 오르다가,
     오늘 고가를 새로 뚫으며 3분 +1.5%↑ & 3분 거래대금 평소의 2배↑ → "주포가 올리는 중". 하루 한 번.
시세는 한투 복수시세(30종목씩, 누적 거래대금) — 1분 간격 기록으로 구간 거래대금을 계산.
"""
import os

# ★ 11-07 대장 — 관심그룹 이름 "isee5"(항상 5종목만 — 많으면 시선이 분산됨). 예전 기본 '끼'·'V알림'도 받음.
GROUP_NAMES = {n.strip().lower() for n in os.getenv("LINA_SPIKE_GROUP", "isee5,끼,v알림").split(",") if n.strip()}
MAX_CODES = 5
A_WIN, A_UP, A_VAL_X, A_VAL_MIN, A_COOL = 5, 3.0, 2.5, 3e8, 30
B_LOOK, B_UP, B_STEP, B_JUMP_WIN, B_JUMP, B_VAL_X = 60, 2.0, 15, 3, 1.5, 2.0


GROUP_OF = {}      # ★ 11-14 {코드: 그 종목이 들어 있던 한투 관심그룹 이름} — 시세 보관 때 출처 구분용(대장 "출처는 명확히")


def group_codes(api, hts_id: str) -> dict:
    """한투 관심그룹 중 이름이 GROUP_NAMES인 것의 {코드: 이름}. 실패면 {}. (종목별 그룹 이름은 GROUP_OF에)"""
    out, grp = {}, {}
    if not hts_id:
        return out
    for gc, gn in (api.get_watchlist_groups(hts_id) or {}).items():
        if (gn or "").strip().lower() in GROUP_NAMES:
            items = dict(api.get_watchlist_stocks(gc, hts_id) or [])
            out.update(items)
            for c in items:
                grp.setdefault(c, (gn or "").strip())
    out = dict(list(out.items())[:MAX_CODES])       # 대장 원칙: 5종목만(더 넣어도 앞 5개만 봄)
    GROUP_OF.clear()
    GROUP_OF.update({c: grp.get(c, "") for c in out})
    return out


def _mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:4])


class SpikeWatch:
    def __init__(self):
        self.date = None
        self.hist = {}      # 코드 → [(분, 가격, 누적거래대금)]
        self.last_a = {}    # 코드 → 마지막 A 알림 분
        self.done_b = set()

    def _reset(self, date):
        if self.date != date:
            self.date, self.hist, self.last_a, self.done_b = date, {}, {}, set()

    @staticmethod
    def _at(h, m):
        """m분 전(이하) 마지막 기록."""
        xs = [x for x in h if x[0] <= m]
        return xs[-1] if xs else None

    def update(self, date: str, hhmm: str, quotes: dict) -> list:
        """1분마다 시세 넣기 → [(코드, 유형 'A'|'B', 설명)]."""
        self._reset(date)
        now = _mins(hhmm)
        out = []
        for code, q in quotes.items():
            p, val = float(q.get("price") or 0), float(q.get("value") or 0)
            if p <= 0:
                continue
            h = self.hist.setdefault(code, [])
            if h and h[-1][0] == now:
                h[-1] = (now, p, val)
            else:
                h.append((now, p, val))
            if len(h) < 6:
                continue
            first = h[0]
            span = max(1, now - first[0])
            avg_per_min = (val - first[2]) / span if val > first[2] else 0
            # A. 5분 안에 +3%↑ & 거래대금 급증
            win = [x for x in h if x[0] >= now - A_WIN]
            lo = min(x[1] for x in win)
            base = self._at(h, now - A_WIN)
            v5 = val - base[2] if base else 0
            up5 = (p / lo - 1) * 100
            if (up5 >= A_UP and v5 >= max(A_VAL_MIN, A_VAL_X * avg_per_min * A_WIN)
                    and now - self.last_a.get(code, -999) >= A_COOL):
                self.last_a[code] = now
                out.append((code, "A", f"5분 +{up5:.1f}% · 5분 거래대금 {v5 / 1e8:.0f}억(평소 {avg_per_min * A_WIN / 1e8:.1f}억)"))
            # B. 계단식(60분 동안 15분 단위로 높아짐) → 오늘 고가 돌파 & 3분 급가속
            if code in self.done_b or now - first[0] < B_LOOK:
                continue
            pts = [self._at(h, now - B_LOOK + k * B_STEP) for k in range(0, B_LOOK // B_STEP + 1)]
            if any(x is None for x in pts):
                continue
            steps = sum(1 for a, b in zip(pts, pts[1:]) if b[1] > a[1])
            rise = (pts[-1][1] / pts[0][1] - 1) * 100
            prev_high = max(x[1] for x in h[:-1])
            b3 = self._at(h, now - B_JUMP_WIN)
            jump = (p / b3[1] - 1) * 100 if b3 else 0
            v3 = val - b3[2] if b3 else 0
            if (steps >= 3 and rise >= B_UP and p > prev_high and jump >= B_JUMP
                    and v3 >= B_VAL_X * avg_per_min * B_JUMP_WIN):
                self.done_b.add(code)
                out.append((code, "B", f"60분 계단식 +{rise:.1f}%({steps}/4칸 상승) → 고가 돌파 3분 +{jump:.1f}% · 3분 대금 {v3 / 1e8:.0f}억"))
        return out
