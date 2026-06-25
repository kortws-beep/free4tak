import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''                # ── 매도 체크 ─────────────────────────────
                self._check_all_sells(pos_mkt_cache)

                # ── 상태 저장 ─────────────────────────────
                self._save_status(cash, total_profit, score_enter, now, pos_mkt_cache)

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                self._notify(
                    f"🛑 [SWING] 봇 종료 | "
                    f"{now_kst().strftime('%Y-%m-%d %H:%M:%S')}",
                    critical=True,
                )
                break
            except Exception as e:'''

new = '''                # ── 5대장주 급락 매수 (30분마다, 정규장 중) ──
                if (is_buy_ok and
                        time.time() - self._last_megacap_check > MEGA_CAP_CHECK_INTERVAL):
                    try:
                        self._check_megacap_dip_buy(psbl_cash)
                    except Exception as e:
                        print(f"⚠️ 5대장주 체크 오류: {e}")
                    self._last_megacap_check = time.time()

                # ── 매도 체크 ─────────────────────────────
                self._check_all_sells(pos_mkt_cache)

                # ── 상태 저장 ─────────────────────────────
                self._save_status(cash, total_profit, score_enter, now, pos_mkt_cache)

                time.sleep(LOOP_SLEEP)

            except KeyboardInterrupt:
                self._notify(
                    f"🛑 [SWING] 봇 종료 | "
                    f"{now_kst().strftime('%Y-%m-%d %H:%M:%S')}",
                    critical=True,
                )
                break
            except Exception as e:'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ run() 루프에 30분 체크 추가")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
