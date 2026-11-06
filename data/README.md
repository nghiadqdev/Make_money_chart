# Dữ liệu cho Kronos

Thư mục này chứa dữ liệu K-line đã chuẩn hoá về đúng format Kronos:

```
timestamps,open,high,low,close,volume,amount
```

- `timestamps`: thời gian (naive, không timezone)
- `open/high/low/close`: giá
- `volume`: khối lượng
- `amount`: giá trị giao dịch (ước lượng `volume * close` nếu nguồn không có)
- Đã sort tăng dần, không trùng timestamp, không NaN, `high/low` bao trùm `open/close`.

## Tổng quan

- **37 file**, **910,558 nến**, **71.1 MB**
- Manifest chi tiết: [`manifest.json`](manifest.json)
- Script thu thập: [`../scripts/collect_data.py`](../scripts/collect_data.py)
- Script kiểm tra model: [`../scripts/smoke_test.py`](../scripts/smoke_test.py)

### Crypto + HK

| File | Số nến | Từ | Đến | Dung lượng |
|---|---:|---|---|---:|
| `data/BTCUSDT_5m_2022-2026.csv` | 499,376 | 2022-01-01 | 2026-09-30 | 40.47 MB |
| `data/ETHUSDT_15m_2022-2026.csv` | 166,459 | 2022-01-01 | 2026-09-30 | 12.73 MB |
| `data/HK_09988_5m_2019-2025.csv` | 93,912 | 2019-11-26 | 2025-09-17 | 5.86 MB |
| `data/SOLUSDT_1h_2021-2026.csv` | 50,362 | 2021-01-01 | 2026-09-30 | 3.52 MB |

### A-share

| File | Số nến | Từ | Đến | Dung lượng |
|---|---:|---|---|---:|
| `data/ashare/000001_1d.csv` | 4,014 | 2010-01-04 | 2026-10-30 | 0.22 MB |
| `data/ashare/000001_5m.csv` | 1,970 | 2026-08-26 | 2026-10-30 | 0.17 MB |
| `data/ashare/000858_1d.csv` | 4,020 | 2010-01-04 | 2026-10-30 | 0.24 MB |
| `data/ashare/002354_1d.csv` | 3,722 | 2010-02-09 | 2026-10-30 | 0.20 MB |
| `data/ashare/002354_5m.csv` | 1,970 | 2026-08-26 | 2026-10-30 | 0.12 MB |
| `data/ashare/002594_1d.csv` | 3,712 | 2011-06-30 | 2026-10-30 | 0.21 MB |
| `data/ashare/300207_1d.csv` | 3,718 | 2011-04-21 | 2026-10-30 | 0.21 MB |
| `data/ashare/300207_5m.csv` | 1,970 | 2026-08-26 | 2026-10-30 | 0.13 MB |
| `data/ashare/300750_1d.csv` | 2,035 | 2018-06-11 | 2026-10-30 | 0.12 MB |
| `data/ashare/600519_1d.csv` | 4,078 | 2010-01-04 | 2026-10-30 | 0.25 MB |
| `data/ashare/600519_5m.csv` | 1,970 | 2026-08-26 | 2026-10-30 | 0.14 MB |
| `data/ashare/600580_1d.csv` | 3,986 | 2010-01-04 | 2026-10-30 | 0.22 MB |
| `data/ashare/600580_5m.csv` | 1,970 | 2026-08-26 | 2026-10-30 | 0.13 MB |
| `data/ashare/601318_1d.csv` | 4,030 | 2010-01-04 | 2026-10-30 | 0.24 MB |
| `data/ashare/688981_1d.csv` | 1,519 | 2020-07-16 | 2026-10-30 | 0.09 MB |

### Việt Nam

| File | Số nến | Từ | Đến | Dung lượng |
|---|---:|---|---|---:|
| `data/vn/ACB_1d.csv` | 1,538 | 2020-12-09 | 2026-10-30 | 0.16 MB |
| `data/vn/BID_1d.csv` | 3,064 | 2015-01-01 | 2026-10-30 | 0.32 MB |
| `data/vn/CTG_1d.csv` | 3,064 | 2015-01-01 | 2026-10-30 | 0.32 MB |
| `data/vn/FPT_1d.csv` | 3,064 | 2015-01-01 | 2026-10-30 | 0.32 MB |
| `data/vn/HDB_1d.csv` | 2,297 | 2018-01-05 | 2026-10-30 | 0.24 MB |
| `data/vn/HPG_1d.csv` | 3,064 | 2015-01-01 | 2026-10-30 | 0.32 MB |
| `data/vn/MSN_1d.csv` | 3,055 | 2015-01-01 | 2026-10-30 | 0.27 MB |
| `data/vn/MWG_1d.csv` | 3,055 | 2015-01-01 | 2026-10-30 | 0.31 MB |
| `data/vn/SSI_1d.csv` | 3,064 | 2015-01-01 | 2026-10-30 | 0.32 MB |
| `data/vn/TCB_1d.csv` | 2,191 | 2018-06-04 | 2026-10-30 | 0.23 MB |
| `data/vn/VCB_1d.csv` | 3,055 | 2015-01-01 | 2026-10-30 | 0.31 MB |
| `data/vn/VHM_1d.csv` | 2,203 | 2018-05-17 | 2026-10-30 | 0.22 MB |
| `data/vn/VIC_1d.csv` | 3,064 | 2015-01-01 | 2026-10-30 | 0.25 MB |
| `data/vn/VNM_1d.csv` | 3,055 | 2015-01-01 | 2026-10-30 | 0.30 MB |

### US

| File | Số nến | Từ | Đến | Dung lượng |
|---|---:|---|---|---:|
| `data/us/AAPL_1d.csv` | 4,233 | 2010-01-04 | 2026-10-30 | 0.48 MB |
| `data/us/MSFT_1d.csv` | 4,233 | 2010-01-04 | 2026-10-30 | 0.47 MB |
| `data/us/NVDA_1d.csv` | 4,233 | 2010-01-04 | 2026-10-30 | 0.48 MB |
| `data/us/SPY_1d.csv` | 4,233 | 2010-01-04 | 2026-10-30 | 0.48 MB |

## Nguồn dữ liệu

| Nguồn | Dữ liệu | Ghi chú |
|---|---|---|
| [Binance Vision](https://data.binance.vision) | BTCUSDT 5m, ETHUSDT 15m, SOLUSDT 1h | Public data, tải theo tháng, không rate-limit |
| AKShare (Sina) | A-share daily 10 mã, 5m 5 mã | 5m chỉ có ~1.970 nến gần nhất |
| yfinance | US daily (AAPL/MSFT/NVDA/SPY), VN daily (14 mã) | Giá đã điều chỉnh (`auto_adjust=True`) |
| File mẫu của repo | HK 09988 5m | `amount` gốc = 0, đã bỏ BOM |

## Dùng với Web UI

```bash
cd webui
python app.py
# http://localhost:7070 -> Load Data -> chọn file trong thư mục data/
```

Web UI quét cả thư mục con (`ashare/`, `vn/`, `us/`) và mặc định dùng cửa sổ **400 nến lịch sử + 120 nến dự đoán**. Mọi file ở đây đều đủ (ít nhất 520 nến).

## Kiểm tra nhanh model

```bash
python scripts/smoke_test.py --data data/BTCUSDT_5m_2022-2026.csv --device mps --pred-len 120
```

Kết quả tham khảo trên BTC 5m (400 nến → 120 nến): **MAPE ~0.4–0.5%**.

## Dùng để dự đoán bằng script

```python
import pandas as pd
from model import Kronos, KronosTokenizer, KronosPredictor

df = pd.read_csv("data/BTCUSDT_5m_2022-2026.csv", parse_dates=["timestamps"])
lookback, pred_len = 400, 120
x_df = df.iloc[:lookback][["open", "high", "low", "close", "volume", "amount"]]
x_ts = df.iloc[:lookback]["timestamps"]
y_ts = df.iloc[lookback:lookback + pred_len]["timestamps"]

tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
predictor = KronosPredictor(model, tokenizer, max_context=512)
pred = predictor.predict(df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                         pred_len=pred_len, T=1.0, top_p=0.9, sample_count=1)
print(pred.head())
```

## Dùng để fine-tune

Sửa `finetune_csv/configs/config_ali09988_candle-5min.yaml`:

```yaml
data:
  data_path: "/Users/diepquangnghia/Desktop/Kronos/data/BTCUSDT_5m_2022-2026.csv"
  lookback_window: 512
  predict_window: 48
```

Rồi:

```bash
cd finetune_csv
python train_sequential.py --config configs/config_ali09988_candle-5min.yaml
```

> Mỗi file CSV là **một chuỗi liên tục**. Không nối nhiều mã vào một file; muốn train nhiều mã hãy sửa dataset để đọc nhiều file.

## Lưu ý

- **Crypto** dùng giờ UTC; **chứng khoán** dùng giờ địa phương của sàn.
- **A-share 5m** chỉ là 1.970 nến gần nhất (Sina), đủ để inference nhưng chưa đủ fine-tune sâu. Muốn nhiều hơn, dùng Đông Tài chính (`--provider em`) sau khi hết rate-limit, hoặc Qlib.
- **VN daily** có một số phiên volume = 0 (Yahoo), nên kiểm tra trước khi fine-tune.
- **HK 09988** có `amount = 0` toàn bộ; Kronos vẫn chạy nhưng tín hiệu `amount` không có tác dụng.
- Dữ liệu chỉ dùng cho học tập/nghiên cứu; tuân thủ điều khoản của từng nguồn.

## Cập nhật thêm

```bash
# Crypto (tải theo tháng)
python scripts/collect_data.py binance_vision --symbol BTCUSDT --interval 5m \
    --start 2026-10 --end 2026-12 --out data/BTCUSDT_5m_2026Q4.csv

# A-share daily
python scripts/collect_data.py akshare --symbol 600580 --period daily \
    --provider sina --start 2026-10-01 --end 2026-12-31 --out data/ashare/600580_1d_new.csv

# Việt Nam daily
python scripts/collect_data.py yfinance --symbol ACB.VN --interval 1d \
    --start 2026-10-01 --end 2026-12-31 --out data/vn/ACB_1d_new.csv
```
