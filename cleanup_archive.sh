#!/bin/bash
# ============================================================
# cleanup_archive.sh — stock_bot 루트/archive 정리 스크립트
# ============================================================
# 절대 삭제(rm) 하지 않습니다. mv로 이동만 합니다.
#
# 사용법:
#   ./cleanup_archive.sh          → dry-run (실제로 옮기지 않고 계획만 출력)
#   ./cleanup_archive.sh --apply  → 실제로 이동 실행
#
# 이동 대상:
#   1) 루트의 fix_*.py, add_*.py  → archive/patches/
#   2) archive/ 바로 아래의 백업 패턴 파일들
#      (.bak, .bak2, .bak_*, .bak5 등, _backup.py, .save, .xx)
#      → archive/backups/
#
# 이동 안 하는 것 (그대로 둠):
#   - archive/ 바로 아래의 정상 .py 파일들 (이미 archive 안이라 충분)
#   - archive/patches/, archive/backups/ 안의 기존 파일들
#   - kikipy, 뉴백업/ 등 패턴이 불명확한 것 — 수동 확인 필요
# ============================================================

set -e
BASE="/home/free4tak/k-bot/stock_bot"
cd "$BASE"

APPLY=false
if [ "$1" == "--apply" ]; then
    APPLY=true
fi

PATCH_DIR="archive/patches"
BACKUP_DIR="archive/backups"

move_count=0

do_move() {
    local src="$1"
    local dst_dir="$2"
    if [ ! -e "$src" ]; then
        return
    fi
    move_count=$((move_count + 1))
    if [ "$APPLY" = true ]; then
        mv -v "$src" "$dst_dir/"
    else
        echo "  [DRY-RUN] mv '$src' -> '$dst_dir/'"
    fi
}

echo "============================================================"
if [ "$APPLY" = true ]; then
    echo "🚀 실제 이동 실행 (--apply)"
else
    echo "🔍 DRY-RUN 모드 — 실제로 옮기지 않습니다 (계획만 출력)"
    echo "   진짜로 옮기려면: ./cleanup_archive.sh --apply"
fi
echo "============================================================"
echo ""

echo "📦 1) 루트의 fix_*.py / add_*.py → $PATCH_DIR/"
for f in fix_*.py add_*.py; do
    do_move "$f" "$PATCH_DIR"
done

echo ""
echo "📦 2) archive/ 루트의 백업 패턴 파일 → $BACKUP_DIR/"
# archive/ 바로 아래(하위 디렉토리 제외)에서 백업 패턴만 골라낸다
for f in archive/*; do
    base=$(basename "$f")
    # 디렉토리는 건너뜀 (archive/patches, archive/backups, archive/backtesta 등)
    if [ -d "$f" ]; then
        continue
    fi
    case "$base" in
        *.bak|*.bak[0-9]|*.bak_*|*_backup.py|*.save|*.xx)
            do_move "$f" "$BACKUP_DIR"
            ;;
    esac
done

echo ""
echo "============================================================"
if [ "$APPLY" = true ]; then
    echo "✅ 완료 — 총 ${move_count}개 파일 이동"
else
    echo "📋 계획 요약 — 총 ${move_count}개 파일 이동 예정"
    echo "   실제로 적용하려면: ./cleanup_archive.sh --apply"
fi
echo "============================================================"
