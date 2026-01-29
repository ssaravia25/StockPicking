import yfinance as yf
import pandas as pd
import logging
import json
import os
from datetime import datetime, timedelta

class MetricsEngine:
    def __init__(self, tickers, cache_file="metrics_cache.json"):
        self.tickers = tickers
        self.cache_file = cache_file
        self.metrics_cache = self._load_cache()

    def _load_cache(self):
        """Load persistent cache from JSON file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error loading cache: {e}")
        return {}

    def _save_cache(self):
        """Save current cache to JSON file."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.metrics_cache, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving cache: {e}")

    def fetch_metrics(self, ticker_symbol):
        """Fetch key financial metrics for a single ticker."""
        yf_ticker = ticker_symbol.replace('.', '-')
        try:
            ticker = yf.Ticker(yf_ticker)
            info = ticker.info
            
            metrics = {
                'Ticker': ticker_symbol,
                'Name': info.get('shortName', 'N/A'),
                'Sector': info.get('sector', 'N/A'),
                'Industry': info.get('industry', 'N/A'),
                'Forward PER': info.get('forwardPE', 'N/A'),
                'PEG Ratio': info.get('trailingPegRatio', 'N/A'),
                'Price/Sales': info.get('priceToSalesTrailing12Months', 'N/A'),
                'ROE (%)': info.get('returnOnEquity', 0) * 100 if info.get('returnOnEquity') else 'N/A',
                'Profit Margin (%)': info.get('profitMargins', 0) * 100 if info.get('profitMargins') else 'N/A',
                'Debt to Equity': info.get('debtToEquity', 'N/A'),
                'Past EPS Growth (%)': info.get('earningsQuarterlyGrowth', 0) * 100 if info.get('earningsQuarterlyGrowth') else 'N/A',
                'Expected Growth (%)': info.get('earningsGrowth', 0) * 100 if info.get('earningsGrowth') else 'N/A',
                'Last_Updated': datetime.now().strftime('%Y-%m-%d')
            }
            return metrics
        except Exception as e:
            logging.error(f"Error fetching metrics for {ticker_symbol}: {e}")
            return None

    def get_ticker_metrics(self, ticker_symbol, force_update=False):
        """Get metrics from cache or fetch them if expired or forced."""
        cached_data = self.metrics_cache.get(ticker_symbol)
        
        needs_update = force_update
        if not needs_update and cached_data:
            last_updated_str = cached_data.get('Last_Updated')
            if last_updated_str:
                last_updated = datetime.strptime(last_updated_str, '%Y-%m-%d')
                if datetime.now() - last_updated > timedelta(days=30):
                    needs_update = True
            else:
                needs_update = True

        if needs_update or not cached_data:
            metrics = self.fetch_metrics(ticker_symbol)
            if metrics:
                self.metrics_cache[ticker_symbol] = metrics
                self._save_cache()
        
        return self.metrics_cache.get(ticker_symbol)
