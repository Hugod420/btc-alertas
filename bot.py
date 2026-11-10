"""
Bot de alertas BTC (15m, 1h, 4h) -> Telegram.
Uso:
  python bot.py          revisa el mercado y manda las alertas nuevas
  python bot.py --test   manda un mensaje de prueba con el estado actual
  python bot.py --dry    igual que normal, pero imprime en vez de mandar a Telegram
Variables de entorno: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, CAPITAL (por defecto 100),
                      RIESGO (por defecto 0.03), APALANCAMIENTO (por defecto 20, solo para mostrar el margen)
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import strategy as st

BOG = ZoneInfo('America/Bogota')
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state.json')
DRY = '--dry' in sys.argv
TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
CHAT = os.environ.get('TELEGRAM_CHAT_ID', '')
CAPITAL = float(os.environ.get('CAPITAL') or 100)
RIESGO = float(os.environ.get('RIESGO') or 0.03)
LEV_SHOW = int(os.environ.get('APALANCAMIENTO') or 20)
st.PARAMS['risk'] = RIESGO
CHART = {'15m': 15, '1h': 60, '4h': 240}
TV = 'https://www.tradingview.com/chart/?symbol=BINANCE%3ABTCUSDT.P&interval={}'
HEATMAP = 'https://www.coinglass.com/pro/futures/LiquidationHeatMap'


# ---------------- utilidades ----------------
def fmt(x):
    return f"{x:,.0f}".replace(',', '.')

def bog(t):
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(BOG)

def send(text):
    if DRY or not TOKEN:
        print('-' * 60); print(text); return
    for _ in range(3):
        try:
            r = requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',
                              json={'chat_id': CHAT, 'text': text, 'parse_mode': 'HTML',
                                    'disable_web_page_preview': True}, timeout=20)
            if r.ok: return
            print('Telegram error', r.status_code, r.text)
        except Exception as e:
            print('Telegram excepción', e)
        time.sleep(3)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'sent': [], 'trades': [], 'closed': [], 'equity': CAPITAL, 'news_sent': [], 'weeks_sent': []}

def save_state(s):
    s['sent'] = s['sent'][-500:]
    s['news_sent'] = s['news_sent'][-200:]
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f, indent=1, default=str)


# ---------------- datos (OKX, BTC-USDT perpetuo) ----------------
def okx_candles(bar, days):
    """Velas cerradas de OKX. bar: '15m' o '1H'."""
    url = 'https://www.okx.com/api/v5/market/history-candles'
    rows, after = [], None
    stop_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    while True:
        params = {'instId': 'BTC-USDT-SWAP', 'bar': bar, 'limit': 100}
        if after: params['after'] = after
        for _ in range(4):
            try:
                r = requests.get(url, params=params, timeout=20).json(); break
            except Exception:
                time.sleep(2)
        data = r.get('data', [])
        if not data: break
        rows += data
        after = data[-1][0]
        if int(after) < stop_ms: break
        time.sleep(0.15)
    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4, 5, 8]]
    df.columns = ['t', 'o', 'h', 'l', 'c', 'v', 'confirm']
    df = df[df.confirm == '1'].drop(columns='confirm').astype(float)
    df['t'] = pd.to_datetime(df.t.astype('int64'), unit='ms')
    return df.drop_duplicates('t').set_index('t').sort_index()

def get_frames():
    m15 = okx_candles('15m', 21)
    h1 = okx_candles('1H', 130)
    # solo velas 4h/diarias completas
    h4 = st.resample(h1, '4h'); d1 = st.resample(h1, '1d')
    last = h1.index[-1] + pd.Timedelta(hours=1)
    h4 = h4[h4.index + pd.Timedelta(hours=4) <= last]
    d1 = d1[d1.index + pd.Timedelta(days=1) <= last]
    frames = {'15m': st.add_indicators(m15), '1h': st.add_indicators(h1),
              '4h': st.add_indicators(h4), '1d': st.add_indicators(d1)}
    return frames


# ---------------- noticias (calendario económico, USD alto impacto) ----------------
def get_news():
    try:
        ev = requests.get('https://nfs.faireconomy.media/ff_calendar_thisweek.json', timeout=20).json()
        out = []
        for e in ev:
            if e.get('country') == 'USD' and e.get('impact') == 'High':
                out.append({'title': e['title'], 'time': datetime.fromisoformat(e['date']).astimezone(timezone.utc)})
        return out
    except Exception as e:
        print('noticias no disponibles', e); return []

def news_near(news, t, hours=2):
    return [n for n in news if abs((n['time'] - t).total_seconds()) <= hours * 3600]


# ---------------- mensajes ----------------
def plan_text(side, price, equity):
    pl = st.trade_plan(side, price, equity)
    lines = []
    for n, (p, w) in enumerate(zip(pl['prices'], st.PARAMS['entries'])):
        notional = w * equity * pl['lev']
        nombre = 'Entrada' if n == 0 else f'Recompra {n}'
        lines.append(f"{n+1}) {nombre}: <b>{fmt(p)}</b> · posición ${notional:,.2f} (margen ${notional/LEV_SHOW:,.2f} a x{LEV_SHOW})")
    return pl, '\n'.join(lines)

def signal_msg(sig, equity, news):
    side = sig['side']; tf = sig['tf']
    head = '🟢 LONG' if side == 1 else '🔴 SHORT'
    t_bog = bog(sig['close_time'].to_pydatetime())
    hst = {1: 'alcista', -1: 'bajista', 0: 'en rango'}[sig['hstruct']]
    lvl_txt = 'barrida de liquidez' if sig['level_tf'] == 'barrida' else f"{'soporte' if side == 1 else 'resistencia'} de {sig['level_tf']}"
    if not sig['entry']:
        riesgo = ' ⚠️ (contra la tendencia mayor)' if sig.get('contra') else ''
        return (f"👀 <b>PRE-AVISO {head[2:]} · {tf}</b>{riesgo}\n"
                f"Las barras del MACD están perdiendo fuerza en zona extrema. Todavía no hay cruce.\n"
                f"Precio {fmt(sig['price'])} · MACD {sig['macd']:.0f} · Estocástico {sig['k']:.0f}\n"
                f"Cerca de {lvl_txt} en {fmt(sig['level'])}\n"
                f"<a href='{TV.format(CHART[tf])}'>Ver gráfico</a>")
    pl, plan = plan_text(side, sig['price'], equity)
    fuerza = '💪 <b>SEÑAL FUERTE</b> (divergencia RSI)\n' if sig['div'] else ''
    if sig.get('contra'):
        fuerza += f"⚠️ <b>SEÑAL DE RIESGO</b>: va contra la tendencia de {st.HIGHER[tf]}. Se registra aparte.\n"
    sweep = ' ✅ con barrida de liquidez' if sig['sweep'] else ''
    tgt = f"{'Próxima resistencia' if side == 1 else 'Próximo soporte'}: {fmt(sig['target'])}" if pd.notna(sig['target']) else ''
    nw = news_near(news, datetime.now(timezone.utc), 2)
    nw_txt = ''.join(f"\n⚠️ Noticia cerca: {n['title']} a las {bog(n['time']):%H:%M}" for n in nw)
    return (f"{head} <b>BTC · {tf}</b>  ({t_bog:%d/%m %H:%M} hora Col.)\n{fuerza}"
            f"Precio: <b>{fmt(sig['price'])}</b>\n"
            f"• MACD {sig['macd']:.0f} " + ("con cruce de líneas" if sig.get('cross', True) else "con cambio de color del histograma (entrada temprana)") + "\n"
            f"• Estocástico {sig['k']:.0f} (llegó a {sig['k_ext']:.0f})\n"
            f"• {lvl_txt.capitalize()} en {fmt(sig['level'])}{sweep}\n"
            f"• Temporalidad mayor: {hst}\n\n"
            f"<b>Plan</b> (cuenta ${equity:,.2f}, pérdida máxima {RIESGO*100:.0f}% = ${equity*RIESGO:,.2f}):\n{plan}\n"
            f"⛔ Stop: <b>{fmt(pl['stop'])}</b> (si se llenan todas las entradas)\n"
            f"🎯 Cerrar 80% cuando el estocástico llegue a {80 if side == 1 else 20}. "
            f"El 20% restante sigue con stop en la entrada.\n{tgt}{nw_txt}\n\n"
            f"<a href='{TV.format(CHART[tf])}'>Gráfico</a> · <a href='{HEATMAP}'>Mapa de liquidez</a>\n"
            f"<i>Revisa noticias y liquidez antes de entrar. Es una alerta, no una orden.</i>")

EV_TXT = {
    'RECOMPRA': '🔁 Recompra ejecutada en {p}',
    'STOP_A_ENTRADA': '🛡 Ya va +0,5% a favor: mueve el stop a {p} (tu entrada)',
    'SALIDA_PARCIAL': '💰 <b>Cierra el 80%</b>: el estocástico llegó al extremo. Precio {p}',
    'SALIDA_RESTO': '✅ <b>Cierra el 20% restante</b> en {p} (MACD en contra o rechazo en el nivel)',
    'STOP': '⛔ <b>Stop tocado</b> en {p}',
    'STOP_EN_ENTRADA': '🛡 Stop en la entrada tocado en {p}: sales sin pérdida',
}


# ---------------- lógica principal ----------------
def run():
    state = load_state()
    now = datetime.now(timezone.utc)
    frames = get_frames()
    ctx = st.build_context(frames)
    news = get_news()
    equity = state['equity']

    # 1) seguimiento de operaciones abiertas
    for tr in [t for t in state['trades'] if t['status'] == 'open']:
        d = frames[tr['tf']]
        ts = pd.Timestamp(tr['time'])
        if ts not in d.index:
            tr['status'] = 'perdida_de_datos'; continue
        out = st.manage(d, d.index.get_loc(ts), tr['side'], st.PARAMS, equity=tr['equity'])
        evs = out['events']
        for ev in evs[tr['events_sent']:]:
            send(f"<b>{'LONG' if tr['side'] == 1 else 'SHORT'} {tr['tf']}</b> del {tr['label']}\n" + EV_TXT[ev[0]].format(p=fmt(ev[2])))
        tr['events_sent'] = len(evs)
        if not out['open']:
            tr['status'] = 'closed'; tr['pnl_pct'] = round(out['pnl_pct'], 3)
            tr['closed_at'] = str(d.index[out['exit_i']])
            equity *= 1 + out['pnl_pct'] / 100
            state['closed'].append({**{k: tr[k] for k in ['tf', 'side', 'label', 'pnl_pct', 'closed_at']},
                                    'riesgo': tr.get('riesgo', False)})
            send(f"📒 Operación {tr['tf']} cerrada. Resultado virtual: <b>{out['pnl_pct']:+.2f}%</b> de la cuenta. "
                 f"Cuenta virtual: ${equity:,.2f}")
    state['trades'] = [t for t in state['trades'] if t['status'] == 'open']
    state['equity'] = equity

    # 2) señales nuevas
    for tf in ['15m', '1h', '4h']:
        start = frames[tf].index[-1] - 3 * st.TF_DELTA[tf]
        sigs = st.signals_for(tf, frames, ctx, st.PARAMS, start=start)
        for _, s in sigs.iterrows():
            key = f"{tf}|{s.time}|{s.side}|{'E' if s.entry else 'P'}"
            if key in state['sent']: continue
            state['sent'].append(key)
            busy = any(t['tf'] == tf for t in state['trades'])
            if busy: continue   # ya hay una operación abierta en esta temporalidad
            send(signal_msg(s.to_dict(), equity, news))
            if s.entry:
                state['trades'].append({'tf': tf, 'time': str(s.time), 'side': int(s.side), 'equity': equity,
                                        'riesgo': bool(s.contra),
                                        'events_sent': 0, 'status': 'open',
                                        'label': f"{bog(s.close_time.to_pydatetime()):%d/%m %H:%M}"})

    # 3) noticias: aviso del día anterior (desde las 7 p. m.) y 1 hora antes
    nb = now.astimezone(BOG)
    for n in news:
        nt = bog(n['time'])
        k1 = f"dia|{n['title']}|{n['time']}"
        if nt.date() == (nb + timedelta(days=1)).date() and nb.hour >= 19 and k1 not in state['news_sent']:
            send(f"📅 Mañana {nt:%d/%m} a las {nt:%H:%M}: <b>{n['title']}</b> (EE. UU., alto impacto). Mucho cuidado con las entradas cerca de esa hora.")
            state['news_sent'].append(k1)
        k2 = f"hora|{n['title']}|{n['time']}"
        if 0 <= (n['time'] - now).total_seconds() <= 3600 and k2 not in state['news_sent']:
            send(f"⏰ En menos de 1 hora ({nt:%H:%M}): <b>{n['title']}</b>. Puede haber movimientos bruscos.")
            state['news_sent'].append(k2)

    # 4) resumen semanal (domingo desde las 7 p. m., hora Colombia)
    wk = f"{nb.isocalendar().year}-{nb.isocalendar().week}"
    if nb.weekday() == 6 and nb.hour >= 19 and wk not in state['weeks_sent']:
        since = (now - timedelta(days=7)).replace(tzinfo=None)
        week = [c for c in state['closed'] if pd.Timestamp(c['closed_at']) >= since]
        if week:
            parts = []
            for nombre, grupo in [('Normales', [c for c in week if not c.get('riesgo')]),
                                  ('⚠️ De riesgo (contra tendencia)', [c for c in week if c.get('riesgo')])]:
                if not grupo: continue
                tot = sum(c['pnl_pct'] for c in grupo); w = sum(c['pnl_pct'] > 0 for c in grupo)
                lines = '\n'.join(f"• {c['tf']} {'LONG' if c['side'] == 1 else 'SHORT'} {c['label']}: {c['pnl_pct']:+.2f}%" for c in grupo)
                parts.append(f"<b>{nombre}</b>: {len(grupo)} operaciones, {w} ganadas, total {tot:+.2f}%\n{lines}")
            body = '\n\n'.join(parts)
        else:
            body = 'Esta semana no se cerró ninguna operación.'
        abiertas = len(state['trades'])
        send(f"📊 <b>Resumen semanal</b>\n{body}\nAbiertas: {abiertas}\nCuenta virtual: ${state['equity']:,.2f} (empezó en ${CAPITAL:,.2f})")
        state['weeks_sent'].append(wk)

    state['last_run'] = now.isoformat(timespec='minutes')
    save_state(state)


def test():
    frames = get_frames()
    lines = ['✅ <b>El bot está conectado</b>\n']
    for tf in ['15m', '1h', '4h']:
        d = frames[tf]; r = d.iloc[-1]
        lines.append(f"{tf}: precio {fmt(r.c)} · MACD {r.macd:.0f} · Estocástico {r.k:.0f}")
    lines.append(f"\nCuenta: ${CAPITAL:,.2f} · pérdida máxima por operación {RIESGO*100:.0f}%")
    send('\n'.join(lines))


if __name__ == '__main__':
    test() if '--test' in sys.argv else run()
