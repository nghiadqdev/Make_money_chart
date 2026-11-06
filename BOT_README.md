# Kronos × KuCoin Paper-Trading Bot

Bot paper-trading tự động, chạy local, dùng **Kronos** để tạo xác suất hướng giá
và **CCXT/KuCoin** để lấy nến 15m (cấu hình được). Không gửi lệnh thật, không cần API secret.

## 1. Kiến trúc

```
config/
  settings.yaml          # toàn bộ tham số runtime
  loader.py              # dataclass + validate + ENV override

core/
  exchange.py            # CCXT KuCoin, retry/backoff/reconnect, OHLCV
  paper_engine.py        # virtual ledger 100 USDT, phí 0.1%, SL/TP, state
  database.py            # SQLite: trades/signals/equity/events
  state_machine.py       # START -> STOPPING -> STOPPED + control file
  types.py               # dataclass/enum dùng chung
  logging_config.py      # rotating file + console

models/
  kronos_signal.py       # wrapper Kronos -> p_up/p_down/expected_return

strategies/
  kronos_trend.py        # probability + EMA trend + ATR

risk_management/
  position_sizer.py      # tranche 25%, risk-based sizing, SL/TP theo ATR
  circuit_breaker.py     # consecutive losses, cooldown, max drawdown
  manager.py             # phê duyệt / từ chối signal

dashboard/
  app.py                 # Flask server (port 7071)
  service.py             # start/stop/force-close, status, direct flatten
  templates/index.html   # UI
  static/style.css       # dark theme
  static/app.js          # polling 2s, chart, toast, bảng dữ liệu

main.py                  # CLI + vòng lặp + lifecycle
```

`model/` (số ít) vẫn là package Kronos gốc, không bị sửa. `models/` (số nhiều)
là lớp adapter mới.

## 2. Luồng xử lý

```
KuCoin OHLCV 15m
      │
      ▼
KronosSignalModel.infer()
  - lấy 400 nến gần nhất
  - sinh 32 path Monte-Carlo song song (predict_batch)
  - return từng path = close_cuối / close_hiện_tại - 1
  - p_up   = tỷ lệ path > +fee_buffer
  - p_down = tỷ lệ path < -fee_buffer
  - score  = p_up - p_down
      │
      ▼
KronosTrendStrategy
  - LONG nếu p_up >= 0.62, expected_return >= 0.3%, EMA trend up
  - SHORT nếu p_down >= 0.38, expected_return <= -0.3%, EMA trend down
  - còn lại HOLD
      │
      ▼
RiskManager
  - circuit breaker (3 lỗi liên tiếp / drawdown 15%)
  - max 4 position, không trùng symbol
  - size = min(risk-based, 1 tranche = equity/4, free_cash)
  - SL = ATR × 1.5 (kẹp 0.6%–3%), TP = ATR × 2.5
      │
      ▼
PaperEngine
  - trừ phí 0.1% khi mở
  - theo dõi SL/TP trên nến đã đóng
  - trừ phí 0.1% khi đóng
  - ghi SQLite + JSON state
```

## 3. Vì sao dùng 15m?

- Kiểm tra 96 lần/ngày thay vì 24 lần/ngày.
- Kronos-small có context 512; 400 nến 15m ≈ 4,2 ngày lịch sử.
- `pred_len = 24` ≈ 6 giờ dự đoán.
- 15m nhạy hơn nhưng nhiều nhiễu và phí hơn 1h; có thể đổi về `30m`/`1h` trong config.

## 4. Cài đặt

```bash
cd ~/Desktop/Kronos
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-bot.txt
```

Nếu đã dùng `.venv` hiện có, chỉ cần:

```bash
pip install ccxt PyYAML
```

Model được tải từ Hugging Face ở lần chạy đầu và cache lại.

## 5. Chạy

```bash
# Chạy 1 vòng để kiểm tra kết nối/model/signal
python main.py run --once

# Chạy liên tục
python main.py run

# Xem trạng thái ledger/risk
python main.py status

# Dừng mềm: chặn lệnh mới, chờ SL/TP
python main.py stop

# Xoá cờ stop
python main.py start

# Reset ledger về 100 USDT
python main.py reset --yes
```

Có thể override nhanh bằng ENV:

```bash
KRONOS_BOT_SYMBOL=ETH/USDT KRONOS_BOT_TIMEFRAME=30m python main.py run --once
```

## 5.0 Vì sao thấy `HOLD` dù `p_up = 100%`?

`p_up = 1.000` nghĩa là **tất cả 32 path Monte-Carlo** đều có return vượt
fee buffer — không phải xác suất 100% của thị trường thật. Đây là ước lượng
từ 32 mẫu của model, có thể rất tự tin khi model đang nhìn một xu hướng mạnh.

`expected_return = 3.34%` là **trung bình return cuối** của 32 path đó.

Signal vẫn có thể là `HOLD` vì **EMA trend filter**:

```
LONG chỉ được phép khi:
  p_up >= long_threshold
  expected_return >= min_expected_return
  EMA(fast) > EMA(slow)  AND  price > EMA(slow)
```

Nếu EMA50 < EMA200 (xu hướng giảm) mà Kronos dự báo tăng, bot sẽ HOLD để tránh
mua ngược trend. Muốn test mô phỏng ngay, tạo config test với:

```yaml
strategy:
  require_trend_alignment: false
```

rồi chạy dashboard với config đó. Đây là thay đổi chiến lược, không phải bug.

## 5.0b 15 phút, lookback mở rộng và feedback loop

### Khung 15 phút

`config/settings.yaml` mặc định `timeframe: "15m"` (96 lần/ngày). Có thể đổi
`30m`, `1h`, `4h`, `1d`. Khung càng ngắn càng nhiều phí/nhiễu; risk engine vẫn
giữ nguyên giới hạn 1% equity/lệnh.

### Lookback mở rộng có trần

```yaml
model:
  lookback: 512
  lookback_growth: 0   # cố định cửa sổ; 0 = không tăng dần
  lookback_max: 512    # trần context của Kronos-small/base
  pred_len: 4          # 4 nến 15m = 1 giờ
  n_paths: 128         # xác suất mịn hơn 32 path
```

Bot lưu `adaptive_lookback` trong state; restart vẫn tiếp tục. **Đây chỉ là
độ dài cửa sổ đầu vào, không phải học thêm**; Kronos-small/base chỉ nhận tối đa
512 nến. Cấu hình hiện tại dùng 512 cố định vì model rất nhạy với lookback:
cùng dữ liệu, 465 nến có thể cho tín hiệu giảm mạnh trong khi 512 nến cho tín
hiệu gần trung tính. Không nên thay đổi lookback liên tục. Muốn context dài hơn
phải dùng Kronos-mini (2048) hoặc đổi kiến trúc.

Sau khi đổi `pred_len`/`lookback`, thống kê dashboard chỉ tính forecast khớp
cấu hình hiện tại. Muốn xoá forecast cũ + calibration (giữ candles/trades):

```bash
python main.py reset-learning --yes
```

### Đánh giá & hiệu chỉnh

Mỗi lần inference, bot lưu vào bảng `forecasts`:

- lookback, `p_up`, `p_down`, `expected_return`
- signal/decision
- horizon end (sau `pred_len` nến)

Khi horizon đã đóng nến, bot lấy giá thực từ bảng `candles`, tính:

- `direction_correct`: dự đoán tăng/giảm có đúng không
- `brier`: sai số xác suất `p_up`
- `abs_error`: sai số expected return

Sau đủ `learning.min_samples` (mặc định 30), `CalibrationManager` chỉnh
`long_threshold` / `short_threshold` theo từng bước nhỏ (mặc định 0.01) và lưu
`var/calibration.json`. Đây là **hiệu chỉnh ngưỡng**, không cập nhật trọng số
Kronos và không tự học online.

### Fine-tune offline từ lịch sử

Bot tự lưu nến vào bảng `candles`. Xuất dataset:

```bash
python scripts/export_training_data.py \
    --db var/kronos_bot.db --symbol BTC/USDT --timeframe 15m \
    --out data/history/BTCUSDT_15m.csv
```

Tạo config và chạy fine-tune (tuỳ chọn `--run`):

```bash
python scripts/finetune_from_db.py \
    --symbol BTC/USDT --timeframe 15m --epochs 3
# hoặc chạy luôn:
python scripts/finetune_from_db.py --symbol BTC/USDT --timeframe 15m --epochs 3 --run
```

Fine-tune là job nặng, nên chạy khi bot đang Stop hoặc dùng máy khác; không chạy
mỗi 15 phút. Sau khi xong, trỏ `model.tokenizer_id` / `model.model_id` vào thư mục
`best_model` mà script in ra.

## 5.1 Dashboard (khuyến nghị)

```bash
python dashboard/app.py
# mở http://127.0.0.1:7071
```

Dashboard có:

- **Start test**: spawn `main.py run` ở process riêng, dùng `.venv` hiện tại.
- **Stop mềm**: chặn lệnh mới, chờ SL/TP; process tự thoát khi hết position.
- **Chốt sổ ngay**: gửi force-close; bot đóng toàn bộ position tại giá thị trường,
  ghi phí + PnL + lịch sử, rồi thoát.
- **Reset**: chỉ khi bot đã dừng, xoá ledger/DB runtime và đưa về 100 USDT.
- Thẻ: equity, free cash, realized/unrealized PnL, exposure, drawdown,
  circuit breaker, last price.
- Bảng: position đang mở (mark, SL, TP, PnL), lịch sử khớp lệnh, signal gần đây.
- Chart equity + log realtime + toast thông báo lãi/lỗ khi lệnh đóng.

Dashboard đọc `var/state.json` + SQLite và tự lấy ticker KuCoin để mark position,
nên PnL hiển thị gần realtime kể cả khi bot đang ngủ giữa các vòng 15m.

Bot chạy ở process con (`start_new_session=True`), nên tắt/mở lại dashboard không
làm dừng bot. PID lưu ở `var/dashboard_bot.pid`.

> Sau khi cập nhật code, phải **restart cả dashboard và bot process** thì UI/order
> engine mới có hiệu lực. Process đang chạy vẫn dùng code cũ trong bộ nhớ.
> Nếu đang có position, dùng Stop/Chốt sổ trên dashboard trước khi restart.

## 6. State machine & graceful shutdown

```
START ──(Ctrl+C / main.py stop / control file)──> STOPPING ──(0 position)──> STOPPED
```

- **START:** sinh signal, mở position mới nếu risk duyệt.
- **STOPPING:** không mở lệnh mới; vẫn kiểm tra SL/TP mỗi vòng.
- **STOPPED:** chỉ khi `len(positions) == 0`.
- `shutdown_force_close_sec`:
  - `0` (mặc định): chờ SL/TP vô hạn, không bao giờ để lại orphan.
  - `>0`: sau N giây kể từ lúc Stop, đóng phần còn lại tại giá thị trường.
- Ctrl+C lần 2 trong lúc STOPPING sẽ force-close ngay.

## 7. Paper ledger & order lifecycle

Mỗi signal được duyệt sẽ mô phỏng đầy đủ vòng đời order như sàn thật:

```
MARKET_ENTRY  → FILLED ngay tại mark price, trừ phí 0.1%
STOP_LOSS     → WORKING (chờ)
TAKE_PROFIT   → WORKING (chờ)
      │
      ├─ giá chạm SL → SL FILLED, TP CANCELLED
      ├─ giá chạm TP → TP FILLED, SL CANCELLED
      └─ stop/force  → SL+TP CANCELLED, MARKET_EXIT FILLED
```

- Bảng `orders` lưu: loại order, side, qty, giá đặt, giá khớp, phí, trạng thái,
  thời điểm tạo/khớp.
- Dashboard có panel **Order mô phỏng & khớp lệnh** hiển thị cả order đang chờ
  (`WORKING`) và order đã khớp (`FILLED`/`CANCELLED`).
- PnL chỉ được chốt khi có exit fill tương ứng; phí được tính ở cả entry và exit.

- Balance ban đầu: **100.0 USDT**.
- Phí taker: **0.1%** mỗi chiều.
- Không leverage, mỗi position chiếm tối đa 1 tranche = equity/4.
- Short được mô phỏng bằng margin reserve; **bỏ qua borrow/funding fee**.
- State lưu tại `var/state.json` (atomic write), lịch sử tại `var/kronos_bot.db`.
- `status` in equity, free cash, position, drawdown, circuit breaker, 10 trade gần nhất.

Công thức:

```
equity = free_cash + Σ margin + Σ unrealized
unrealized(long)  = (mark - entry) × qty
unrealized(short) = (entry - mark) × qty
pnl = gross_pnl - entry_fee - exit_fee
```

## 8. Risk

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `tranches` | 4 | vốn chia 4 phần, không all-in |
| `risk_per_trade_pct` | 1% | mất tối đa 1% equity nếu SL |
| `sl_atr_mult` | 1.5 | SL = 1.5 × ATR |
| `tp_atr_mult` | 2.5 | TP = 2.5 × ATR |
| `min_sl_pct` / `max_sl_pct` | 0.6% / 3% | kẹp SL |
| `max_open_positions` | 4 | tối đa 4 position |
| `circuit_breaker_consecutive_losses` | 3 | 3 lỗi liên tiếp → cooldown |
| `circuit_breaker_cooldown_min` | 120 | cooldown 2 giờ |
| `max_drawdown_pct` | 15% | vượt → halt, cần `reset` |

## 9. Test

```bash
pytest tests/test_paper_engine.py tests/test_risk.py \
       tests/test_signal_classifier.py tests/test_state_machine.py -q
```

Test không gọi mạng, không load model.

## 10. Giới hạn & hướng mở rộng

- **Paper only:** chưa có hàm đặt lệnh thật. Muốn live phải thêm lớp execution
  riêng, xác thực API key, idempotency, và guard `live_trading=true`.
- **Một symbol:** config hiện tại chạy 1 cặp. Muốn nhiều cặp, lặp danh sách
  symbol và dùng chung RiskManager/PaperEngine (state đã hỗ trợ nhiều position).
- **Kronos probability** là Monte-Carlo từ model sinh giá, không phải classifier
  đã train. Có thể fine-tune riêng một head phân loại nếu có nhãn.
- **Short** bỏ qua funding/borrow; **slippage** chưa mô hình hoá (có thể thêm
  `slippage_bps` vào PaperEngine).
- **KuCoin** có thể chặn IP một số khu vực; wrapper sẽ báo lỗi rõ ràng.
- **State/DB** nên backup trước khi đổi version schema.
