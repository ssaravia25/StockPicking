# Deploying to Streamlit Cloud

## Quick Start

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Configure for Streamlit Cloud deployment"
   git push origin main
   ```

2. **Deploy on Streamlit Cloud**
   - Go to [share.streamlit.io](https://share.streamlit.io)
   - Click "New app"
   - Select your repository: `ssaravia25/StockPicking`
   - Main file path: `streamlit_app.py`
   - Click "Deploy"

## Important Configuration

### Memory Optimization

Your app loads ~400 tickers with 10 years of historical data. Streamlit Cloud free tier has **1GB RAM limit**.

**Current status**: The app may crash or timeout during initial data load.

**Solutions if you hit memory limits:**

1. **Reduce universe size** (recommended for free tier):
   Edit `src/ranking_backtest.py` line 16-18 and reduce the ticker list to ~50-100 tickers.

2. **Upgrade to Streamlit Cloud paid tier**:
   - Community: $20/month (2GB RAM)
   - Team: $250/month (8GB RAM)

3. **Optimize data loading**:
   - Reduce period from `10y` to `5y` or `3y`
   - Load data on-demand instead of all at once

### Files Changed

- ✅ Created `.streamlit/config.toml` (app styling & settings)
- ✅ Created `streamlit_app.py` (entry point)
- ✅ Fixed deprecation warnings (`use_container_width` → `width`)
- ✅ Updated `.gitignore` to exclude secrets

### Secrets Management

For Google Sheets integration or other credentials:

1. Go to your Streamlit Cloud app settings
2. Click "Secrets" in the sidebar
3. Add your secrets in TOML format:

```toml
# Example secrets structure
[google]
credentials = '''
{
  "type": "service_account",
  "project_id": "your-project-id",
  ...
}
'''
```

Access secrets in code:
```python
import streamlit as st
credentials = st.secrets["google"]["credentials"]
```

## Troubleshooting

### App crashes during startup
- **Cause**: Out of memory loading all tickers
- **Fix**: Reduce ticker universe size in `src/ranking_backtest.py`

### "This app has exceeded its resource limits"
- **Cause**: Too much data cached
- **Fix**:
  1. Click "Reboot app" in Streamlit Cloud
  2. Reduce data period or universe size
  3. Consider upgrading tier

### Slow performance
- **Expected**: First load takes 2-5 minutes to fetch all data
- **Optimization**: Data is cached after first run
- **Tip**: Use `force_refresh` checkbox sparingly

## Current App Features

✅ Live stock scanning with Stage 2 detection
✅ Backtest performance visualization
✅ Trade explorer with entry/exit markers
✅ Financial metrics from Yahoo Finance
✅ Quality ranking system
✅ Risk/reward calculations

## Monitoring

After deployment, monitor your app at:
- **App URL**: `https://[your-app-name].streamlit.app`
- **Analytics**: Streamlit Cloud dashboard shows usage & errors
- **Logs**: Available in Streamlit Cloud interface

## Need Help?

If the app is unstable:
1. Check logs in Streamlit Cloud
2. Verify memory usage
3. Test locally first: `streamlit run streamlit_app.py`
4. Consider alternative hosting (Railway, Render) if memory is insufficient
