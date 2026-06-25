import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 메서드를 _save_status 직전(클래스 마지막 메서드들 영역)에 추가
old = '''    # ============================================================
    # 상태 저장
    # ============================================================
    def _save_status(self, cash: int, total_profit: float,'''

new = '''    # ============================================================
    # ★ 5대장주 급락 매수 (전용 슬롯, 2026-06-23 추가)
    # ============================================================
    def _check_megacap_dip_buy(self, psbl_cash: int):
        """
        삼성전자/SK하이닉스/삼성전기/SK스퀘어/현대차 — 5대장주 중
        최근 10일 최고가 대비 -15% 이상 하락한 종목이 있으면 1개 매수.
        기존 MAX_POSITIONS 슬롯과는 완전히 별개(전용 1슬롯).
        매수 후에는 일반 positions/peak_tracker에 합류시켜
        기존 ATR 추세추종(_check_all_sells)이 그대로 관리하게 함.
        """
        # 이미 5대장주 중 보유중인 종목이 있으면 스킵 (전용슬롯 1개)
        held_megacaps = [c for c in MEGA_CAP_CODES if c in self.positions]
        if held_megacaps:
            return

        candidates = []
        for code, name in MEGA_CAP_CODES.items():
            try:
                ohlc = self.api.get_daily_ohlc(code, days=MEGA_CAP_LOOKBACK_DAYS)
                if not ohlc or len(ohlc) < 3:
                    continue
                highs = [c["high"] for c in ohlc if c.get("high", 0) > 0]
                if not highs:
                    continue
                recent_high = max(highs)
                mdata = self.api.get_market_data(code)
                if not mdata:
                    continue
                current = float(mdata.get("stck_prpr", 0))
                if current <= 0 or recent_high <= 0:
                    continue
                drop_rate = (current - recent_high) / recent_high
                if drop_rate <= MEGA_CAP_DROP_THRESHOLD:
                    candidates.append((drop_rate, code, name, current, mdata))
            except Exception as e:
                print(f"⚠️ 5대장주 {name} 조회 오류: {e}")
                continue

        if not candidates:
            return

        # 가장 많이 빠진 종목 1개만 매수
        candidates.sort(key=lambda x: x[0])
        drop_rate, code, name, current, mdata = candidates[0]

        amount = min(MEGA_CAP_BUY_AMT, psbl_cash)
        if amount < current:
            print(f"⏭️ 5대장주 {name} 패스 — 예산({amount:,}) < 주가({current:,.0f})")
            return

        qty = max(int(amount / current), 1)
        ok, orgno, odno = self.api.buy(code, current, amount, {code: name})
        if not ok:
            print(f"❌ 5대장주 매수 실패: {name}")
            return

        print(f"🛒 [5대장주 급락매수] {name}({code}) | 10일최고대비:{drop_rate:+.1%} | "
              f"{qty}주 @ {current:,.0f}")
        self._notify(
            f"🛒 [5대장주 급락매수] {name}\\n"
            f"10일 최고가 대비: {drop_rate:+.1%}\\n"
            f"{qty}주 @ {current:,.0f}원",
            critical=True,
        )

        self.positions[code] = {"entry_price": current, "qty": qty}
        self._pending_orders[code] = (orgno or "", odno or "", qty)

        # ATR 기반 손절/목표가 — 기존 추세추종 로직에 그대로 편입
        _atr_rate = 0.0
        try:
            ohlc2 = self.api.get_daily_ohlc(code, days=20)
            if ohlc2 and len(ohlc2) >= 14:
                highs2  = [c["high"]  for c in ohlc2[:15]]
                lows2   = [c["low"]   for c in ohlc2[:15]]
                closes2 = [c["close"] for c in ohlc2[:15]]
                trs = []
                for i in range(1, len(closes2)):
                    tr = max(highs2[i] - lows2[i],
                              abs(highs2[i] - closes2[i-1]),
                              abs(lows2[i]  - closes2[i-1]))
                    trs.append(tr)
                atr = sum(trs) / len(trs) if trs else 0
                _atr_rate = atr / current if current > 0 else 0
        except Exception:
            pass

        if _atr_rate > 0:
            _atr_val = current * _atr_rate
            _stop    = round(current - _atr_val * 2.0, 0)
            _tgt     = round(current + _atr_val * 3.0, 0)
        else:
            _atr_val = current * 0.07 / 2.0
            _stop    = round(current * 0.93, 0)
            _tgt     = round(current * 1.12, 0)

        import datetime as _dt
        self.peak_tracker[code] = {
            "peak_rate":   0.0,
            "peak_price":  current,
            "stage":       0,
            "buy2_done":   True,
            "buy1_price":  current,
            "stop_price":  _stop,
            "target1":     _tgt,
            "target_next": _tgt,
            "atr_val":     _atr_val,
            "buy_date":    _dt.date.today().isoformat(),
        }
        if code not in self.code_name_map:
            self.code_name_map[code] = name

    # ============================================================
    # 상태 저장
    # ============================================================
    def _save_status(self, cash: int, total_profit: float,'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ _check_megacap_dip_buy 메서드 추가")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
