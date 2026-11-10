# Bot de alertas BTC (15m · 1h · 4h)

Revisa Bitcoin cada 15 minutos y te manda a Telegram tu señal de MACD + Estocástico cuando se cumplen todas las reglas. También te avisa cuándo recomprar, cuándo mover el stop, cuándo cerrar el 80%, cuándo cerrar el resto, las noticias fuertes de EE. UU. y un resumen cada domingo. **Solo manda alertas: no opera por ti.**

## Reglas (resumen)

**Entrada LONG** (SHORT es lo mismo al revés):
1. MACD (12,26,9) por debajo del nivel de la temporalidad: 15m −0,25% del precio · 1h −0,4% · 4h −0,7%. Con BTC en 75.000 eso es ≈ −190 / −300 / −525.
2. **En 1h y 4h:** se entra apenas las barras del MACD pasan de rojo fuerte a rojo claro (entrada temprana) o cuando la línea azul cruza la naranja, lo que pase primero. **En 15m:** el cambio de color llega como pre-aviso y la entrada es con el cruce.
3. Estocástico (14,1,3) en 20 o menos en las últimas 6 velas y todavía por debajo de 70 (sin haber llegado al otro extremo).
4. El mínimo donde rebotó el precio (últimas 6 velas) está a menos de 1% de un soporte (máximos y mínimos de 15m, 1h, 4h y diario), o acaba de barrer liquidez: perforó el soporte y volvió a cerrar encima.
5. Tendencia de la temporalidad mayor (15m mira 1h, 1h mira 4h, 4h mira diario), medida por estructura: máximos y mínimos más altos = alcista, más bajos = bajista. Si la señal va en contra, igual llega, pero marcada como **⚠️ SEÑAL DE RIESGO**, y en el resumen semanal se cuenta aparte.
6. Si hay divergencia RSI, la alerta dice "SEÑAL FUERTE".

**Pre-aviso (15m):** cuando las barras del MACD pasan de rojo fuerte a rojo claro con el estocástico abajo, antes del cruce.

**Gestión:**
- 1 entrada + 3 recompras, cada una 1% más abajo (en short, más arriba). Tamaños 15% / 10% / 10% / 10%.
- Stop 1% después de la última recompra.
- El tamaño se calcula para que, si se llenan todas y toca el stop, pierdas máximo el 3% de la cuenta (variable `RIESGO`).
- Cuando va +0,5% a favor, el stop pasa a la entrada.
- Cuando el estocástico llega a 80 (20 en short), se cierra el 80%. El 20% restante sale si el MACD se cruza en contra o hay rechazo en un nivel.

## Instalación (desde el celular)

### 1. Crear el bot de Telegram
1. En Telegram, abre **@BotFather** y escribe `/newbot`.
2. Ponle un nombre (por ejemplo "Alertas BTC") y un usuario que termine en `bot`.
3. BotFather te da un **token** (algo como `123456:ABC-...`). Cópialo.
4. Abre tu bot nuevo y mándale cualquier mensaje ("hola").
5. Abre en el navegador `https://api.telegram.org/bot<TOKEN>/getUpdates` (cambia `<TOKEN>` por tu token). Busca `"chat":{"id":` y copia ese número: es tu **chat id**.

### 2. Subir el bot a GitHub
1. En github.com toca **+ → New repository**. Nombre: `btc-alertas`. Elige **Public** (en público, GitHub Actions es gratis sin límite; el token queda guardado en secreto y no se ve). Créalo.
2. Sube los archivos: **Add file → Upload files**, y selecciona `bot.py`, `strategy.py`, `requirements.txt` y `README.md`.
3. El archivo del horario va en una carpeta especial: **Add file → Create new file**, escribe como nombre `.github/workflows/bot.yml` (con las barras, así se crean las carpetas) y pega el contenido de `bot.yml`. Guarda con **Commit changes**.

### 3. Guardar el token y la configuración
En el repositorio: **Settings → Secrets and variables → Actions**.
- Pestaña **Secrets → New repository secret**:
  - `TELEGRAM_TOKEN` = el token de BotFather
  - `TELEGRAM_CHAT_ID` = tu chat id
- Pestaña **Variables → New repository variable** (opcionales):
  - `CAPITAL` = tu capital en USD (por defecto 100)
  - `RIESGO` = pérdida máxima por operación (por defecto `0.03` = 3%)
  - `APALANCAMIENTO` = el apalancamiento que usas, solo para mostrarte el margen (por defecto 20)

### 4. Prenderlo
1. Pestaña **Actions** → si pregunta, toca **I understand… enable**.
2. Elige **Bot alertas BTC → Run workflow**, marca **Mandar mensaje de prueba** y ejecútalo.
3. En 1-2 minutos debe llegarte "✅ El bot está conectado" a Telegram.
4. Desde ahí corre solo cada 15 minutos. GitHub a veces se retrasa unos minutos en las horas de más uso.

## Notas
- Los precios vienen de OKX (BTC-USDT perpetuo), porque Binance bloquea los servidores de GitHub. La diferencia con Binance o Bitunix suele ser de pocos dólares.
- `state.json` guarda las alertas enviadas y las operaciones virtuales. No lo borres, o el bot repetirá alertas.
- Para cambiar reglas (niveles del MACD, recompras, stop), edita `PARAMS` al inicio de `strategy.py`.
- Si GitHub pausa el bot por inactividad (pasa tras 60 días sin cambios en el repositorio), entra a Actions y vuelve a activarlo.
- Backtest con estas reglas y riesgo de 3%: gana el 70-96% de las operaciones. En 1h y 4h fue positivo (2020-24: +95% y +63%; 2025-26: +9% y +15%). En 15m fue negativo (2020-24: −38%; 2025-26: −7%) porque salen muchas más señales falsas. Usa las alertas para medir tu criterio antes de arriesgar dinero.
