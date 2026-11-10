"""
Estrategia MACD + Estocástico con soportes/resistencias, estructura de mercado
y promedio limitado. Este mismo archivo lo usan el backtest y el bot.
"""
import numpy as np
import pandas as pd

TF_RULE = {'15m': '15min', '1h': '1h', '4h': '4h', '1d': '1D'}
TF_DELTA = {'15m': pd.Timedelta(minutes=15), '1h': pd.Timedelta(hours=1),
            '4h': pd.Timedelta(hours=4), '1d': pd.Timedelta(days=1)}
HIGHER = {'15m': '1h', '1h': '4h', '4h': '1d'}
PIVOT_N = {'15m': 8, '1h': 6, '4h': 4, '1d': 3}      # velas a cada lado para marcar un máximo/mínimo

# ---------------- Parámetros (se calibran con datos; ver README) ----------------
PARAMS = {
    # MACD mínimo como fracción del precio (con BTC en 75.000: 15m ≈ 190, 1h ≈ 300, 4h ≈ 525)
    'macd_thr': {'15m': 0.0025, '1h': 0.004, '4h': 0.007},
    'stoch_low': 20, 'stoch_high': 80, 'stoch_look': 6,
    'near_sr': 0.01,            # máxima distancia al soporte/resistencia (1%)
    'entries': [0.15, 0.10, 0.10, 0.10],   # proporción de cada entrada: 1 entrada + 3 recompras
    'step': 0.01,               # distancia entre recompras (1%)
    'stop_after': 0.01,         # stop 1% después de la última recompra
    'risk': 0.03,               # pérdida máxima de la cuenta si toca el stop con todas las entradas
    'max_lev': 50,
    'partial': 0.80,            # % que se cierra cuando el estocástico llega a 80/20
    'be_trigger': 0.005,        # al ir +0.5% a favor del promedio, el stop pasa a la entrada
    'early_tfs': ['1h', '4h'],  # temporalidades donde se entra con el cambio de color del histograma
}

# ---------------- Indicadores ----------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def add_indicators(d):
    d = d.copy()
    d['macd'] = ema(d.c, 12) - ema(d.c, 26)
    d['sig'] = ema(d.macd, 9)
    d['hist'] = d.macd - d.sig
    lo, hi = d.l.rolling(14).min(), d.h.rolling(14).max()
    d['k'] = 100 * (d.c - lo) / (hi - lo)
    d['kd'] = d.k.rolling(3).mean()
    delta = d.c.diff()
    up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d['rsi'] = 100 - 100 / (1 + up / dn)
    return d

def resample(d1, tf):
    if tf in ('15m',):
        return d1
    return d1.resample(TF_RULE[tf]).agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum'}).dropna()

# ---------------- Máximos/mínimos (pivotes), soportes/resistencias y estructura ----------------
def pivots(d, n):
    """Devuelve DataFrame de pivotes confirmados: tiempo de confirmación, precio, tipo."""
    lo, hi = d.l.values, d.h.values
    out = []
    for i in range(n, len(d) - n):
        if lo[i] == lo[i-n:i+n+1].min():
            out.append((d.index[i+n], lo[i], 'L', d.index[i]))
        if hi[i] == hi[i-n:i+n+1].max():
            out.append((d.index[i+n], hi[i], 'H', d.index[i]))
    p = pd.DataFrame(out, columns=['confirm', 'price', 'kind', 'at'])
    return p

def structure_series(d, piv, tf):
    """Tendencia por estructura: +1 si máximos y mínimos crecientes, -1 si decrecientes, 0 rango.
       Se conoce al cierre de la vela que confirma el pivote."""
    piv = piv.copy()
    piv['known'] = piv.confirm + TF_DELTA[tf]
    piv = piv.sort_values('known')
    lastH, lastL = [], []
    times, vals = [], []
    for _, r in piv.iterrows():
        (lastH if r.kind == 'H' else lastL).append(r.price)
        lastH, lastL = lastH[-2:], lastL[-2:]
        st = 0
        if len(lastH) == 2 and len(lastL) == 2:
            if lastH[1] > lastH[0] and lastL[1] > lastL[0]: st = 1
            elif lastH[1] < lastH[0] and lastL[1] < lastL[0]: st = -1
        times.append(r.known); vals.append(st)
    s = pd.Series(vals, index=pd.DatetimeIndex(times))
    return s[~s.index.duplicated(keep='last')]

def build_context(frames):
    """frames: dict tf -> DataFrame OHLC con indicadores. Devuelve niveles y estructura por tf."""
    ctx = {}
    for tf, d in frames.items():
        p = pivots(d, PIVOT_N[tf])
        p['known'] = p.confirm + TF_DELTA[tf]
        ctx[tf] = {'piv': p, 'struct': structure_series(d, p, tf)}
    return ctx

# ---------------- Señales ----------------
def signals_for(tf, frames, ctx, P=PARAMS, start=None):
    """Calcula señales de la temporalidad tf. Devuelve DataFrame con una fila por vela señal."""
    d = frames[tf]
    thr = P['macd_thr'][tf]
    m, s, k = d.macd, d.sig, d.k
    cross_up = (m > s) & (m.shift() <= s.shift())
    cross_dn = (m < s) & (m.shift() >= s.shift())
    rel = m / d.c
    look = P['stoch_look']
    # el estocástico tocó la zona extrema en las últimas velas y todavía no se ha ido lejos de ella
    # el estocástico tocó la zona extrema en las últimas velas y todavía no llegó al extremo contrario
    L = cross_up & (rel < -thr) & (k.rolling(look).min() <= P['stoch_low']) & (k < 70)
    S = cross_dn & (rel > thr) & (k.rolling(look).max() >= P['stoch_high']) & (k > 30)
    # pre-aviso: histograma pierde fuerza en zona extrema
    # cambio de color del histograma (rojo fuerte -> rojo claro) con el estocástico abajo:
    # en 1h y 4h es ENTRADA temprana; en 15m es solo PRE-AVISO (en 15m da demasiadas señales falsas)
    preL = (d['hist'] < 0) & (d['hist'] > d['hist'].shift()) & (d['hist'].shift() <= d['hist'].shift(2)) & (rel < -thr) & (k.rolling(look).min() <= P['stoch_low']) & (k < 50)
    preS = (d['hist'] > 0) & (d['hist'] < d['hist'].shift()) & (d['hist'].shift() >= d['hist'].shift(2)) & (rel > thr) & (k.rolling(look).max() >= P['stoch_high']) & (k > 50)
    if tf in P['early_tfs']:
        L = L | preL; S = S | preS
    cand = d.index[(L | S | preL | preS).fillna(False).values]
    if start is not None:
        cand = cand[cand >= pd.Timestamp(start)]
    # niveles de esta temporalidad y de las mayores
    tfs = [tf] + [t for t in ['15m', '1h', '4h', '1d'] if list(TF_RULE).index(t) > list(TF_RULE).index(tf)]
    lv = pd.concat([ctx[t]['piv'].assign(tf=t) for t in tfs if t in ctx])
    lv = lv.sort_values('known')
    lv_known = lv.known.values; lv_price = lv.price.values; lv_kind = lv.kind.values; lv_tf = lv.tf.values
    hs = ctx[HIGHER[tf]]['struct'] if HIGHER.get(tf) in ctx else None
    rows = []
    near = P['near_sr']
    for t in cand:
        i = d.index.get_loc(t)
        close_time = t + TF_DELTA[tf]
        # niveles conocidos hasta ahora (últimos 60 días)
        mask = (lv_known <= np.datetime64(close_time)) & (lv_known >= np.datetime64(close_time - pd.Timedelta(days=60)))
        price = d.c.iat[i]
        side = 1 if (bool(L.iat[i]) or bool(preL.iat[i])) else -1
        is_entry = bool(L.iat[i]) if side == 1 else bool(S.iat[i])
        # estructura de la temporalidad mayor
        hstruct = 0
        if hs is not None:
            hh = hs[hs.index <= close_time]
            hstruct = int(hh.iat[-1]) if len(hh) else 0
        # contra la tendencia de la temporalidad mayor: se avisa, pero marcada como señal de riesgo
        contra = (side == 1 and hstruct == -1) or (side == -1 and hstruct == 1)
        lows = lv_price[mask & (lv_kind == 'L')]; highs = lv_price[mask & (lv_kind == 'H')]
        tfs_l = lv_tf[mask & (lv_kind == 'L')]; tfs_h = lv_tf[mask & (lv_kind == 'H')]
        level = None; sweep = False; level_tf = None
        lo2 = d.l.iloc[max(0, i-1):i+1].min(); hi2 = d.h.iloc[max(0, i-1):i+1].max()
        if side == 1:
            sup = lows[lows <= price * 1.002]
            if len(sup):
                j = np.argmax(sup); lvl = sup[j]
                # distancia medida desde el mínimo reciente (donde rebotó), no desde el precio del cruce
                if d.l.iloc[max(0, i-5):i+1].min() / lvl - 1 <= near: level, level_tf = lvl, tfs_l[lows <= price * 1.002][j]
            # barrida: perforó un soporte en las últimas 2 velas y cerró de vuelta encima
            sw = lows[(lo2 < lows * 0.9995) & (price > lows)]
            if len(sw): sweep = True; level = level or sw.max(); level_tf = level_tf or 'barrida'
            target = highs[highs > price * 1.002]
            target = target.min() if len(target) else np.nan
        else:
            res = highs[highs >= price * 0.998]
            if len(res):
                j = np.argmin(res); lvl = res[j]
                if 1 - d.h.iloc[max(0, i-5):i+1].max() / lvl <= near: level, level_tf = lvl, tfs_h[highs >= price * 0.998][j]
            sw = highs[(hi2 > highs * 1.0005) & (price < highs)]
            if len(sw): sweep = True; level = level or sw.min(); level_tf = level_tf or 'barrida'
            target = lows[lows < price * 0.998]
            target = target.max() if len(target) else np.nan
        if level is None: continue
        # divergencia RSI (precio hace nuevo extremo y RSI no) en las últimas 40 velas
        w = d.iloc[max(0, i-40):i+1]
        if side == 1:
            div = (w.l.iloc[-10:].min() < w.l.iloc[:-10].min()) and (w.rsi.iloc[-10:].min() > w.rsi.iloc[:-10].min())
        else:
            div = (w.h.iloc[-10:].max() > w.h.iloc[:-10].max()) and (w.rsi.iloc[-10:].max() < w.rsi.iloc[:-10].max())
        rows.append(dict(time=t, close_time=close_time, tf=tf, side=side, entry=is_entry, price=price,
                         macd=m.iat[i], k=k.iat[i], k_ext=(k.iloc[max(0,i-look+1):i+1].min() if side == 1 else k.iloc[max(0,i-look+1):i+1].max()), level=level, level_tf=level_tf, sweep=sweep,
                         target=target, div=bool(div), hstruct=hstruct, contra=bool(contra), cross=bool(cross_up.iat[i] if side == 1 else cross_dn.iat[i])))
    return pd.DataFrame(rows)

# ---------------- Plan de la operación ----------------
def trade_plan(side, entry, equity, P=PARAMS):
    w = P['entries']
    prices = [entry * (1 - side * P['step'] * a) for a in range(len(w))]
    stop = prices[-1] * (1 - side * P['stop_after'])
    # pérdida si se llenan todas y toca el stop, por unidad de apalancamiento
    loss_per_lev = sum(wi * abs(p / stop - 1) for wi, p in zip(w, prices)) * equity
    fees = sum(w) * equity * 0.0014
    lev = P['risk'] * equity / (loss_per_lev + fees * 1)  # aproximado; se limita abajo
    lev = max(1, min(P['max_lev'], int(lev)))
    return dict(prices=prices, stop=stop, lev=lev,
                margins=[round(wi * equity, 2) for wi in w])

# ---------------- Gestión de la operación vela a vela (backtest y seguimiento del bot) ----------------
def manage(d, i0, side, P=PARAMS, equity=100.0, fee=0.0014):
    """Simula la operación desde la vela i0+1. Devuelve dict con resultado (en % de la cuenta)."""
    o, h, l, c, k, m, s = [d[x].values for x in ['o', 'h', 'l', 'c', 'k', 'macd', 'sig']]
    if i0 + 1 >= len(d):   # la vela de entrada todavía no ha cerrado
        return dict(open=True, pnl_pct=0.0, n_entries=0, lev=0, exit_i=len(d), events=[],
                    entry=None, stop0=None, avg=None, stop=None, open_frac=1.0)
    e = o[i0 + 1]
    plan = trade_plan(side, e, equity, P)
    lev = plan['lev']; w = P['entries']
    filled = [(e, w[0])]
    stop = plan['stop']; be = False; part_done = False
    pnl = 0.0; open_frac = 1.0; j = i0 + 1; n = len(d)
    events = []
    while j < n:
        # recompras
        for idx in range(len(filled), len(w)):
            p = plan['prices'][idx]
            if (side == 1 and l[j] <= p) or (side == -1 and h[j] >= p):
                filled.append((p, w[idx])); events.append(('RECOMPRA', d.index[j], p))
            else:
                break
        wsum = sum(x[1] for x in filled); avg = sum(x[0] * x[1] for x in filled) / wsum
        notional = wsum * equity * lev
        # stop
        if (side == 1 and l[j] <= stop) or (side == -1 and h[j] >= stop):
            pnl += open_frac * (side * (stop / avg - 1) * notional - fee * notional)
            events.append(('STOP' if not be else 'STOP_EN_ENTRADA', d.index[j], stop)); break
        fav = (h[j] / avg - 1) if side == 1 else -(l[j] / avg - 1)
        if not be and fav >= P['be_trigger']:
            be = True; stop = avg * (1 + side * 0.0015); events.append(('STOP_A_ENTRADA', d.index[j], stop))
        # salida parcial con estocástico
        if not part_done and ((side == 1 and k[j] >= P['stoch_high']) or (side == -1 and k[j] <= P['stoch_low'])):
            frac = P['partial']
            pnl += frac * (side * (c[j] / avg - 1) * notional - fee * notional)
            open_frac -= frac; part_done = True
            be = True; stop = avg * (1 + side * 0.0015) if side * (c[j] - avg) > 0 else stop
            events.append(('SALIDA_PARCIAL', d.index[j], c[j]))
            if open_frac <= 1e-9: break
        elif part_done:
            # resto: sale si el MACD se cruza en contra o rechazo en resistencia/soporte (mecha larga)
            rng = h[j] - l[j] + 1e-9
            rej = ((h[j] - max(o[j], c[j])) / rng > 0.5) if side == 1 else ((min(o[j], c[j]) - l[j]) / rng > 0.5)
            cross_against = (side == 1 and m[j] < s[j] and m[j-1] >= s[j-1]) or (side == -1 and m[j] > s[j] and m[j-1] <= s[j-1])
            if cross_against or rej:
                pnl += open_frac * (side * (c[j] / avg - 1) * notional - fee * notional)
                events.append(('SALIDA_RESTO', d.index[j], c[j])); open_frac = 0; break
        j += 1
    done = j < n
    wsum = sum(x[1] for x in filled); avg = sum(x[0] * x[1] for x in filled) / wsum
    return dict(open=not done, pnl_pct=pnl / equity * 100, n_entries=len(filled), lev=lev, exit_i=j,
                events=events, entry=e, stop0=plan['stop'], avg=avg, stop=stop, open_frac=open_frac)
