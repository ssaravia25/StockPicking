# Trading Matrix - Gradio on Google Colab

## 🎯 Why Gradio + Colab?

**Streamlit Cloud** has 1GB RAM limit which causes crashes with ~400 tickers.

**Google Colab + Gradio:**
- ✅ **FREE** with more RAM (12GB standard, 15GB with Colab Pro)
- ✅ **Public sharing** via gradio.live links (72 hours)
- ✅ **GPU access** available (faster processing)
- ✅ **No deployment** needed - just run and share

---

## 🚀 Quick Start (3 Steps)

### Option A: Use the Notebook (Easiest)

1. **Open in Colab:**
   - Go to: https://colab.research.google.com/
   - Click **File → Upload notebook**
   - Upload: `TradingMatrix_Colab.ipynb`

2. **Run all cells** (Runtime → Run all)

3. **Click the public link** that appears

**That's it!** The link works for 72 hours.

---

### Option B: Manual Setup

```bash
# 1. In Colab, install dependencies
!pip install gradio yfinance pandas numpy plotly scipy

# 2. Clone repo
!git clone https://github.com/ssaravia25/StockPicking.git
%cd StockPicking

# 3. Run app
!python gradio_app.py
```

Click the public URL (https://xxxxx.gradio.live)

---

## 📊 Features

### 🚀 Live Market Scan Tab
- Click "Run Live Scan" button
- See TOP Ideas (Quality Ranking > 85%)
- View Buy Alerts (Stage 2 + Score < 4)
- Full universe snapshot

### 📊 Performance Hub Tab
- Select exit strategy (trend_guardian, sma_trailing, score_decay)
- Click "Run Backtest"
- See equity curve vs SPY
- View current holdings

### 🔍 Ticker Deep Dive Tab
- Select ticker from dropdown
- Click "Analyze Ticker"
- View price chart with entry/exit signals
- See financial metrics and trade history

---

## ⚡ Performance

| Metric | Time |
|--------|------|
| First load | 2-3 min |
| After cache | < 5 sec |
| Live scan | 10-20 sec |
| Backtest | 5-10 sec |
| Ticker analysis | 2-5 sec |

**First load is slow** because it downloads 2 years of data for ~400 tickers. After that, everything is cached and fast.

---

## 🔧 Customization

### Reduce Memory Usage

Edit `src/ranking_backtest.py` line 16-18:

```python
# Reduce universe to top 100 tickers
universe = [
    'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'AAPL', 'TSLA', 'JPM', 'V', 'UNH',
    # ... keep only ~100 tickers
]
```

### Use GPU Runtime

1. In Colab: **Runtime → Change runtime type**
2. Select **GPU**
3. Click **Save**

This gives you more RAM and faster processing.

---

## 🆚 Gradio vs Streamlit

| Feature | Streamlit Cloud (Free) | Gradio on Colab |
|---------|----------------------|------------------|
| RAM | 1GB | 12-15GB |
| CPU | Shared | Dedicated |
| GPU | ❌ | ✅ Available |
| Cost | Free | Free |
| Link duration | Permanent | 72 hours |
| Setup time | 10 min deploy | 2 min run |
| Performance | Crashes with 400 tickers | Handles easily |

---

## 📝 Sharing Your App

### Public Link
When you run the app, Gradio gives you a link like:
```
https://abc123xyz.gradio.live
```

**This link:**
- ✅ Works anywhere (no login needed)
- ✅ Valid for 72 hours
- ✅ Handles multiple users
- ❌ Expires after 72 hours (just re-run to get new link)

### Keep-Alive Tips

Colab sessions timeout after 90 minutes of inactivity.

**To keep alive:**
1. **Colab Pro** ($10/month): Longer sessions
2. **Auto-refresh**: Open link in browser with auto-refresh extension
3. **Background tab**: Keep Colab tab open

---

## 🔒 Security Notes

**Your code is public** (GitHub repo is public)

**Credentials:**
- ✅ `.env` is gitignored
- ✅ No secrets in code
- ✅ API calls from Colab (not exposed)

**Data:**
- ✅ All data fetched from Yahoo Finance (public)
- ✅ No sensitive user data
- ✅ Cached locally in Colab session

---

## 🐛 Troubleshooting

### "Session crashed" or "Out of memory"

1. Use GPU runtime (more RAM)
2. Reduce ticker universe to ~100 stocks
3. Restart runtime and re-run

### "Module not found"

Re-run Cell 1 (install dependencies)

### "Repository not found"

Check your GitHub repo is public:
```bash
!git clone https://github.com/ssaravia25/StockPicking.git
```

### Link expired

Just re-run Cell 3 to get a new link (takes < 5 seconds)

---

## 🎓 Next Steps

### Make it Permanent

If you want a permanent URL instead of 72-hour links:

**Option 1: Hugging Face Spaces** (Recommended)
- Free, permanent hosting
- Better than Streamlit Cloud
- Can use Gradio or Streamlit
- Simple deploy from GitHub

**Option 2: Google Cloud Run**
- Your Dockerfile is ready
- Free tier available
- More complex setup

**Option 3: Railway/Render**
- Free tiers available
- Easy setup
- Good for Python apps

I can help you set up any of these if you want something permanent!

---

## 📧 Support

Questions? Email: sgseaux@gmail.com

---

Made by **SFinance** | Not investment advice
