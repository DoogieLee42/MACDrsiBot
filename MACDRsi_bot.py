import time
import hmac
import hashlib
import requests
from decimal import Decimal, ROUND_DOWN

# === 사용자 설정 ===
API_KEY            = "8KlioNyZRxRDBWNQVpLuD7Rb7B5bb8bscAEC3OnyNwctCKztkzSQGsqhBxT9JdFA"
API_SECRET         = "2sJQtouL7OSgDGwbJS0c3qekw5AmNaklcdXxt2d1dPmU316uzofayJMoCXTseZMI"
BASE_URL           = "https://api.binance.com"
SYMBOL             = "XRPUSDT"   # 거래 심볼
ASSET              = "XRP"       # 매도 시 사용할 코인 코드

KLINE_INTERVAL     = "5m"        # 1m, 3m, 5m, 15m ... 원하는 분봉
KLINE_LIMIT        = 100         # 캔들 몇 개씩 불러올지 (RSI 14 + MACD 26 등을 고려)
TRADE_INTERVAL_SEC = 300         # 매수/매도 판단 & 실행 주기 (초)
LOG_INTERVAL_SEC   = 60          # 그 사이에 로그 찍는 주기 (초)

# 찬스별 비율 (0~1 사이)
GOOD_BUY_RATIO     = 0.2  # RSI 40~50 + 매수 신호 → '매수 찬스'
BEST_BUY_RATIO     = 0.5  # RSI < 40  + 매수 신호 → '절호의 매수 찬스'
GOOD_SELL_RATIO    = 0.2  # RSI 50~60 + 매도 신호 → '매도 찬스'
BEST_SELL_RATIO    = 0.5  # RSI >= 60 + 매도 신호 → '절호의 매도 찬스'

# === 공통 함수 ===
def get_signature(params):
    qs = "&".join(f"{k}={v}" for k,v in params.items())
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def get_account_info():
    params = {"timestamp": int(time.time()*1000)}
    params["signature"] = get_signature(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    return requests.get(f"{BASE_URL}/api/v3/account", params=params, headers=headers).json()

def get_lot_size(symbol):
    info = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol}).json()
    f = next(f for f in info["symbols"][0]["filters"] if f["filterType"]=="LOT_SIZE")
    return Decimal(f["minQty"]), Decimal(f["stepSize"])

def adjust_quantity(symbol, raw_qty):
    min_qty, step = get_lot_size(symbol)
    adj = (raw_qty // step) * step
    return adj.quantize(step, rounding=ROUND_DOWN) if adj >= min_qty else Decimal("0")

def get_klines(symbol, interval, limit):
    return requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit}
    ).json()

def compute_rsi(closes, period=14):
    deltas = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    gains  = [d if d>0 else 0 for d in deltas]
    losses = [-d if d<0 else 0 for d in deltas]
    avg_gain = sum(gains[:period])/period
    avg_loss = sum(losses[:period])/period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain*(period-1) + gains[i]) / period
        avg_loss = (avg_loss*(period-1) + losses[i]) / period
    rs = avg_gain/avg_loss if avg_loss!=0 else 0
    return 100 - (100/(1+rs))

def compute_ema(values, period):
    ema = []
    alpha = 2/(period+1)
    for i, v in enumerate(values):
        ema.append(v if i==0 else (v-ema[-1])*alpha + ema[-1])
    return ema

def compute_macd(closes, fast=12, slow=26, signal_p=9):
    ema_fast    = compute_ema(closes, fast)
    ema_slow    = compute_ema(closes, slow)
    macd_line   = [f-s for f,s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line, signal_p)
    return macd_line, signal_line

def market_buy(symbol, ratio):
    acct      = get_account_info()
    free_usdt = Decimal(next(x for x in acct["balances"] if x["asset"]=="USDT")["free"])
    price     = Decimal(requests.get(
        f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}
    ).json()["price"])
    raw_qty   = free_usdt * Decimal(ratio) / price
    qty       = adjust_quantity(symbol, raw_qty)
    if qty == 0:
        return {"msg":"최소 수량 미달, 매수 생략"}
    params = {
        "symbol":symbol, "side":"BUY", "type":"MARKET",
        "quantity":str(qty), "timestamp":int(time.time()*1000)
    }
    params["signature"] = get_signature(params)
    return requests.post(f"{BASE_URL}/api/v3/order", params=params, headers={"X-MBX-APIKEY":API_KEY}).json()

def market_sell(symbol, asset, ratio):
    acct      = get_account_info()
    free_coin = Decimal(next(x for x in acct["balances"] if x["asset"]==asset)["free"])
    raw_qty   = free_coin * Decimal(ratio)
    qty       = adjust_quantity(symbol, raw_qty)
    if qty == 0:
        return {"msg":"최소 수량 미달, 매도 생략"}
    params = {
        "symbol":symbol, "side":"SELL", "type":"MARKET",
        "quantity":str(qty), "timestamp":int(time.time()*1000)
    }
    params["signature"] = get_signature(params)
    return requests.post(f"{BASE_URL}/api/v3/order", params=params, headers={"X-MBX-APIKEY":API_KEY}).json()

# === 메인 루프 ===
if __name__ == "__main__":
    while True:
        # 1) 데이터 로드 & 지표 계산
        klines       = get_klines(SYMBOL, KLINE_INTERVAL, KLINE_LIMIT)
        closes       = [float(k[4]) for k in klines]
        macd_line, sig_line = compute_macd(closes)
        m_curr, s_curr     = macd_line[-1], sig_line[-1]
        m_prev, s_prev     = macd_line[-2], sig_line[-2]
        diff_curr          = abs(m_curr - s_curr)
        diff_prev          = abs(m_prev - s_prev)
        rsi_val            = compute_rsi(closes[-(14+1):])

        buy_signal  = (m_curr < s_curr) and (diff_curr < diff_prev)
        sell_signal = (m_curr > s_curr) and (diff_curr < diff_prev)

        # 2) 로그 출력 (TRADE_INTERVAL_SEC 동안)
        start = time.time()
        while time.time() - start < TRADE_INTERVAL_SEC:
            dominant = "MACD" if m_curr > s_curr else "Signal"
            sig_txt  = "매수 신호" if buy_signal else ("매도 신호" if sell_signal else "신호 없음")
            print(f"[{time.strftime('%H:%M:%S')}] {sig_txt} | 우위: {dominant} | 차이: {diff_curr:.6f} | RSI: {rsi_val:.2f}")
            time.sleep(LOG_INTERVAL_SEC)

        # 3) 찬스 판별 & 주문
        chance, ratio = None, 0
        if buy_signal:
            if rsi_val < 32:
                chance, ratio = "절호의 매수 찬스", BEST_BUY_RATIO
            elif rsi_val < 42:
                chance, ratio = "매수 찬스",     GOOD_BUY_RATIO
            action = market_buy
        elif sell_signal:
            if rsi_val >= 68:
                chance, ratio = "절호의 매도 찬스", BEST_SELL_RATIO
            elif rsi_val >= 58:
                chance, ratio = "매도 찬스",       GOOD_SELL_RATIO
            action = lambda s,r: market_sell(s, ASSET, r)
        else:
            action = None

        if chance:
            kind = "매수" if buy_signal else "매도"
            print(f"🔥 {chance}! 비율 {ratio*100:.0f}%로 {kind} 실행")
            print(action(SYMBOL, ratio))
        else:
            print("🎯 찬스 아님, 주문 생략\n")
