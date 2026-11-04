"""
semi_map.py — 대한민국 반도체 공급망 지도 (2026-11-05, 대장 "전체를 정리해 두고 참조하면 빠르게 접근")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
출처: 부산달팽이 경제연구소 「반도체 완전정복 14부 — 대한민국 반도체 산업 최종 투자지도」(2026-08-23,
https://blog.naver.com/bigbro2/224387349120). 원문 텍스트는 docs/reference/semi_map_part14.txt.
핵심 생각: 뉴스를 보고 '무슨 테마지?'가 아니라 ① 공정의 무엇이 바뀌나 → ② 작업량·Content가 느는 곳 → ③ 실제 양산 공급사.
  GROUPS — 공정·역할별 종목(이름 그대로, 각 종목의 한 줄 설명)
  CYCLE  — 유형별로 실적을 움직이는 변수(장비=CAPEX, 부품=Installed Base×가동률 …)
  NEWS   — '이런 뉴스가 나오면 → 볼 그룹·대표 종목'(원문 33~38장·46장 치트시트)
쓰는 곳(예정): 이벤트 섹터 엔진·MTN 아침 '오늘 주목'이 반도체일 때 세부 공정으로 좁히기, SUB 그룹 재선정 참고.
코드는 codes()가 kr_stock_master에서 이름으로 찾음(없으면 빠짐 — check()로 확인).
원문 이름이 사명 변경된 5곳은 지금 이름으로 적음(에이피티씨→브이엠, 디바이스이엔지→디바이스, 오션브릿지→티이엠씨씨엔에스,
아이원스→한솔아이원스, 라온테크→라온로보틱스).
"""
import os
import sqlite3

GROUPS = {
    "제조·파운드리": {"삼성전자": "DRAM·HBM·NAND·Foundry 종합", "SK하이닉스": "HBM 핵심 제조사·DRAM·NAND", "DB하이텍": "Foundry"},
    "전공정 장비": {
        "원익IPS": "CVD·ALD·Thermal 증착, Memory·Foundry CAPEX", "주성엔지니어링": "ALD·CVD 박막(GAA·High-k)",
        "유진테크": "LPCVD·Furnace 배치 증착·열처리", "테스": "Memory 증착", "HPSP": "고압 수소 어닐링(GAA Interface)",
        "피에스케이": "PR 제거 Dry Strip·Ash", "제우스": "Wafer 세정·Wet", "케이씨텍": "CMP·세정 장비+CMP 소재",
        "브이엠": "Plasma Etch(원문 '에이피티씨' — 사명 변경)", "뉴파워프라즈마": "Plasma Source·Module", "GST": "Scrubber·Chiller",
        "유니셈": "Scrubber·Chiller", "에스티아이": "Chemical 공급·Reflow(HBM 후공정)", "씨앤지하이테크": "Chemical 공급장치",
        "엘오티베큠": "Dry Vacuum Pump", "워트": "초정밀 온습도 제어", "디바이스": "세정·공정장비(원문 '디바이스이엔지')", "케이엔제이": "공정장비·SiC 부품"},
    "소재": {
        "솔브레인": "Etchant·Cleaning Chemical", "동진쎄미켐": "Photoresist(EUV)", "이엔에프테크놀로지": "고순도 Process Chemical",
        "원익머트리얼즈": "Specialty Gas", "후성": "특수가스", "디엔에프": "Precursor(CVD·ALD)", "레이크머티리얼즈": "Precursor",
        "한솔케미칼": "H2O2 등 Chemical", "램테크놀러지": "Wet Chemical", "티이엠씨씨엔에스": "특수가스·Chemical 공급(원문 '오션브릿지' — 합병 후 사명)",
        "제이아이테크": "Precursor", "덕산테코피아": "Precursor", "메카로": "Precursor·Heater", "비씨엔씨": "합성 Quartz 부품",
        "와이엠티": "PCB·전자 Chemical"},
    "부품·소모품": {
        "원익QnC": "Quartz Parts", "월덱스": "Silicon·Quartz Parts", "티씨케이": "SiC Parts(Etch)", "하나머티리얼즈": "Silicon Electrode·Ring",
        "미코": "Ceramic Parts", "코미코": "Chamber Cleaning·Coating", "한솔아이원스": "부품 Cleaning·Coating(원문 '아이원스')",
        "에프에스티": "Pellicle(EUV)", "에스앤에스텍": "Blank Mask", "아스플로": "Gas Line·Valve", "디케이락": "Valve·Fitting",
        "한양이엔지": "Fab Utility"},
    "검사·계측": {
        "넥스틴": "Wafer Defect Inspection", "오로스테크놀로지": "Overlay Metrology", "파크시스템스": "AFM 나노계측",
        "인텍플러스": "HBM·Package 3D 검사", "기가비스": "기판 검사(FC-BGA)", "펨트론": "3D Inspection", "고영": "3D 검사"},
    "후공정 장비·Test": {
        "한미반도체": "HBM TC Bonder", "테크윙": "Test Handler", "프로텍": "Dispense·Underfill", "이오테크닉스": "Laser Dicing·Marking",
        "제너셈": "후공정 자동화", "코세스": "Laser·Packaging", "라온로보틱스": "Wafer Transfer Robot(원문 '라온테크')", "로체시스템즈": "공정장비",
        "미래산업": "Test Handler", "디아이": "Memory Tester", "와이씨": "Memory Test(DRAM·HBM)", "유니테스트": "Memory Tester",
        "엑시콘": "Memory·SSD Tester", "네오셈": "SSD·Memory Tester(CXL)", "아이텍": "비메모리 Test", "큐알티": "신뢰성 Test",
        "두산테스나": "Wafer Test(비메모리)", "에이팩트": "Memory Test·후공정"},
    "소켓·프로브": {
        "ISC": "Elastomer·Pogo·Burn-in Socket", "리노공업": "LEENO Pin·IC Socket", "티에스이": "Probe Card·Interface·Socket",
        "오킨스전자": "IC Test Socket", "마이크로컨텍솔": "IC Test Socket", "티에프이": "Test Socket·Board",
        "마이크로투나노": "MEMS Probe Card(HBM)", "샘씨엔에스": "Ceramic STF(Probe Card)", "메가터치": "Test Pin"},
    "OSAT·패키징": {
        "하나마이크론": "Memory Packaging·Test", "SFA반도체": "Memory Packaging·Test", "LB세미콘": "Bumping·Test",
        "네패스": "Bumping·Fan-out", "네패스아크": "비메모리 Test", "시그네틱스": "Packaging·Test", "윈팩": "Memory Packaging"},
    "기판·PCB": {
        "삼성전기": "FC-BGA·Glass Substrate", "대덕전자": "FC-BGA·PCB", "심텍": "Memory Module PCB·Substrate",
        "해성디에스": "Lead Frame·Substrate", "코리아써키트": "PCB·Substrate", "이수페타시스": "고다층 PCB(AI Server·800G)",
        "티엘비": "Memory Module PCB", "타이거일렉": "Test·Probe용 PCB", "비에이치": "FPCB(반도체 직접노출 아님)",
        "인터플렉스": "FPCB", "뉴프렉스": "FPCB", "디에이피": "PCB", "네오티스": "PCB 가공 Tool"},
    "PCB 소재": {
        "두산": "Low-loss CCL(AI Network)", "롯데에너지머티리얼즈": "Copper Foil(배터리/PCB 비중 구분)",
        "솔루스첨단소재": "Copper Foil", "이녹스첨단소재": "전자·PCB 소재", "덕산하이메탈": "Solder Ball",
        "엠케이전자": "Bonding Wire·Solder Ball", "SKC": "Glass Substrate(Absolics)"},
    "팹리스·IP": {
        "가온칩스": "ASIC Design", "에이디테크놀로지": "ASIC Design", "오픈엣지테크놀로지": "Memory Interface IP",
        "칩스앤미디어": "Video Codec IP", "텔레칩스": "차량 SoC", "어보브반도체": "MCU", "제주반도체": "Memory Fabless",
        "동운아나텍": "Analog", "LX세미콘": "DDI", "퀄리타스반도체": "SerDes IP", "파두": "SSD Controller",
        "자람테크놀로지": "Network Chip", "아이앤씨": "Connectivity", "앤씨앤": "Vision Chip", "넥스트칩": "차량 Vision SoC",
        "에이엘티": "비메모리 Test"},
}

CYCLE = {
    "전공정 장비": "Fab CAPEX (Wafer Starts × 공정 횟수 × 장비 강도)",
    "소재": "Wafer Starts × 공정 횟수 × 웨이퍼당 사용량 × 등급 — 가동률이 핵심",
    "부품·소모품": "Installed Base × 가동률 × 교체 주기 — 신규 장비 안 사도 가동률 오르면 늘어남",
    "후공정 장비·Test": "HBM CAPEX·Stack 복잡도 / 칩 수 × Test 단계 × Test 시간",
    "소켓·프로브": "칩 수 × Test 단계 × Interface Content",
    "기판·PCB": "칩·서버 수 × 면적 × 층수 × 소재 등급",
    "PCB 소재": "서버·네트워크 고속화(400G→800G→1.6T) — Low-loss CCL·동박",
}

NEWS = {   # 뉴스 → (먼저 볼 공정, 대표 종목)
    "HBM 증설": ("DRAM·Bonding → Test·Packaging·기판",
                 ["SK하이닉스", "삼성전자", "한미반도체", "테크윙", "ISC", "리노공업", "티에스이", "인텍플러스", "삼성전기", "대덕전자"]),
    "NAND 고단화": ("Deposition·HAR Etch → Cleaning·Chamber Parts·Inspection",
                    ["원익IPS", "주성엔지니어링", "유진테크", "테스", "브이엠", "솔브레인", "티씨케이", "하나머티리얼즈", "월덱스", "원익QnC", "넥스틴"]),
    "DRAM 미세화": ("Lithography·Deposition → Etch·Cleaning·Inspection", ["동진쎄미켐", "원익IPS", "넥스틴"]),
    "2nm GAA": ("ALD·Selective Etch → Annealing(HPA)·Metrology",
                ["주성엔지니어링", "원익IPS", "유진테크", "HPSP", "넥스틴", "오로스테크놀로지", "파크시스템스", "동진쎄미켐", "솔브레인"]),
    "Fab 신규투자": ("전공정 장비 → 이후 부품·Service", ["원익IPS", "유진테크", "GST", "유니셈"]),
    "Fab 가동률 상승": ("소재 → Quartz·SiC·Si 부품", ["솔브레인", "원익QnC", "티씨케이"]),
    "AI GPU 신제품": ("HBM·Advanced Packaging → Test·FC-BGA → AI Server PCB → Low-loss CCL",
                      ["삼성전기", "대덕전자", "이수페타시스", "두산"]),
    "AI Server 증가": ("GPU·HBM → 고다층 PCB", ["이수페타시스"]),
    "800G·1.6T 네트워크": ("Network PCB → Low-loss CCL → Copper Foil", ["이수페타시스", "두산", "솔루스첨단소재", "롯데에너지머티리얼즈"]),
    "Chiplet 확대": ("Advanced Packaging → Bonding·Test", ["한미반도체", "인텍플러스"]),
    "Hybrid Bonding": ("Bonding → CMP·Cleaning·Inspection", ["한미반도체", "케이씨텍", "넥스틴"]),
    "Glass Substrate": ("TGV → Laser·Plating·Inspection (개발/양산 단계 구분)", ["삼성전기", "SKC", "이오테크닉스", "기가비스"]),
}

CHECKLIST = ["무엇을 만드나", "어느 공정에 들어가나", "DRAM·HBM·NAND·Foundry·AI 중 어디 노출", "고객은 누구",
             "Qualification 끝났나", "양산 매출 있나", "무엇이 늘면 매출이 느나", "ASP·Mix 개선", "진입장벽",
             "주가가 기대를 얼마나 반영했나"]


def _master(db: str = None) -> dict:
    if db is None:
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lina_bot", "kr_theme_finance.db")
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {n.replace(" ", "").upper(): k for k, n in c.execute("SELECT code, name FROM kr_stock_master")}
    finally:
        c.close()


def codes(names, master: dict = None) -> dict:
    """이름들 → {코드: 이름}(마스터에 없는 이름은 빠짐)."""
    m = master if master is not None else _master()
    return {m[n.replace(" ", "").upper()]: n for n in names if n.replace(" ", "").upper() in m}


def group_codes(group: str, master: dict = None) -> dict:
    return codes(GROUPS.get(group, {}), master)


def news_codes(key: str, master: dict = None) -> dict:
    return codes(NEWS.get(key, ("", []))[1], master)


def find(name: str):
    """종목 이름 → [(그룹, 설명)] — '이 종목 반도체 어디 소속?'"""
    k = name.replace(" ", "").upper()
    return [(g, d[n]) for g, d in GROUPS.items() for n in d if n.replace(" ", "").upper() == k]


def check(master: dict = None) -> list:
    """마스터에서 못 찾는 이름(오타·상장폐지·이름 변경 확인용)."""
    m = master if master is not None else _master()
    every = {n for d in GROUPS.values() for n in d} | {n for _, ns in NEWS.values() for n in ns}
    return sorted(n for n in every if n.replace(" ", "").upper() not in m)


if __name__ == "__main__":
    miss = check()
    total = len({n for d in GROUPS.values() for n in d})
    print(f"반도체 지도 {total}종목 · 그룹 {len(GROUPS)} · 뉴스 {len(NEWS)} — 마스터에 없는 이름 {len(miss)}: {miss}")
