import pandas as pd
import mplfinance as mpf
import os

def generate_and_save_chart(df: pd.DataFrame, stock_name: str, file_name: str = "temp_chart.png"):
    """
    Pandas DataFrame(OHLCV)을 받아 캔들 차트를 그리고 이미지 파일로 저장합니다.
    """
    # 데이터프레임 인덱스가 DatetimeIndex인지 확인 (mplfinance 필수 조건)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # 최근 100~150봉 정도만 잘라서 보여주는 것이 모바일에서 예쁩니다.
    df_plot = df.tail(120).copy()

    # 원석 님의 주력 지표인 200일 이동평균선이 데이터에 있다면 차트에 얹습니다.
    apdict = []
    if 'ma200' in df_plot.columns:
        # ma200 선을 붉은색(실선)으로 추가
        apdict.append(mpf.make_addplot(df_plot['ma200'], color='red', width=1.5, title="MA200"))

    # 스타일 설정 (야후 파이낸스 스타일 + 캔들 차트)
    mc = mpf.make_marketcolors(up='r', down='b', edge='inherit', wick='inherit', volume='inherit')
    s  = mpf.make_mpf_style(marketcolors=mc, gridstyle=':')

    # 차트 생성 및 저장 (volume=True로 거래량도 함께 표시)
    mpf.plot(
        df_plot, 
        type='candle', 
        volume=True, 
        addplot=apdict,
        style=s, 
        title=f"\n{stock_name} (Daily)",
        savefig=file_name,
        figratio=(10, 6),
        tight_layout=True
    )
    
    return file_name