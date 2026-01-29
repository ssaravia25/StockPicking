import yfinance as yf
import pandas as pd
import logging

class MetricsEngine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.metrics_cache = {}

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
                'Expected Growth (%)': info.get('earningsGrowth', 0) * 100 if info.get('earningsGrowth') else 'N/A'
            }
            return metrics
        except Exception as e:
            logging.error(f"Error fetching metrics for {ticker_symbol}: {e}")
            return None

    def get_ticker_metrics(self, ticker_symbol):
        """Get metrics from cache or fetch them."""
        if ticker_symbol not in self.metrics_cache:
            metrics = self.fetch_metrics(ticker_symbol)
            if metrics:
                self.metrics_cache[ticker_symbol] = metrics
        return self.metrics_cache.get(ticker_symbol)
