"""
Trading Matrix Dashboard - Gradio Version for Google Colab
Lightweight alternative to Streamlit with public sharing capability
"""

import gradio as gr
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.ranking_backtest import RankingEngine, universe, get_stats
from src.metrics_engine import MetricsEngine
import yfinance as yf
from datetime import datetime

# Initialize engines (cached globally)
print("Initializing engines... This may take 2-3 minutes on first run.")
engine = RankingEngine(universe)
engine.load_data()
metrics_engine = MetricsEngine(universe, cache_file="metrics_cache_v3.json")
print(f"✅ Loaded data for {len(engine.data_cache)} tickers")

# === Tab 1: Live Scan Functions ===
def run_live_scan():
    """Run live market scan for buy alerts"""
    # Calculate Quality Rankings
    qr_rankings = metrics_engine.calculate_quality_rankings()

    results = []
    for t, df in engine.data_cache.items():
        last_row = df.iloc[-1]

        # Proactive fetching for candidates
        is_candidate = (last_row["Stage_Daily"] == "Stage 2") and (last_row["Score"] <= 6)
        qr_val = "N/A"
        if is_candidate:
            m = metrics_engine.get_ticker_metrics(t)
            if m:
                qr_val = qr_rankings.get(t, "N/A")

        results.append({
            "Ticker": t,
            "Stage": last_row["Stage_Daily"],
            "Score": round(last_row["Score"], 1),
            "Price": round(last_row["Close"], 2),
            "Trend Template": "✅ Pass" if last_row.get("Trend_Template", False) else "❌ Fail",
            "Quality Ranking (QR)": qr_val
        })

    summary_df = pd.DataFrame(results)

    # TOP Ideas
    top_ideas = summary_df[
        (summary_df["Stage"] == "Stage 2") &
        (summary_df["Score"] <= 4) &
        (pd.to_numeric(summary_df["Quality Ranking (QR)"], errors='coerce') >= 85)
    ].sort_values("Quality Ranking (QR)", ascending=False)

    # Buy Alerts
    best_candidates = summary_df[
        (summary_df["Stage"] == "Stage 2") &
        (summary_df["Score"] <= 4)
    ].sort_values("Score")

    top_html = f"<h3>💎 TOP Ideas: {len(top_ideas)} Premium Opportunities</h3>" if not top_ideas.empty else ""

    return (
        top_html,
        top_ideas if not top_ideas.empty else pd.DataFrame(),
        best_candidates,
        summary_df.sort_values("Score")
    )

# === Tab 2: Backtest Functions ===
def run_backtest_analysis(exit_strategy):
    """Run backtest with selected strategy"""
    res_equity, res_trades, current_slots = engine.run(exit_strategy=exit_strategy)

    # Get SPY benchmark
    spy = yf.Ticker("SPY").history(start=res_equity.index[0], end=res_equity.index[-1])
    spy.index = spy.index.tz_localize(None)
    spy_daily = spy["Close"].pct_change().fillna(0)
    spy_equity = (1 + spy_daily).cumprod() * 100
    spy_res = pd.DataFrame({"Equity": spy_equity}, index=spy.index)

    # Calculate stats
    stats_tg = get_stats(res_equity)
    stats_spy = get_stats(spy_res)

    # Create equity plot
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=res_equity.index,
        y=res_equity["Equity"],
        name="Strategy",
        line=dict(color='blue', width=3)
    ))
    fig.add_trace(go.Scatter(
        x=spy_res.index,
        y=spy_res["Equity"],
        name="SPY Benchmark",
        line=dict(color='gray', dash='dash')
    ))
    fig.update_layout(
        title=f"Equity Growth - {exit_strategy.replace('_', ' ').title()}",
        xaxis_title="Date",
        yaxis_title="Equity (Start = 100)",
        height=500,
        template="plotly_dark"
    )

    # Create exposure plot
    fig_exp = go.Figure()
    fig_exp.add_trace(go.Scatter(
        x=res_equity.index,
        y=res_equity["Long_Count"],
        fill='tozeroy',
        name="Active Positions",
        line=dict(color='green', width=1)
    ))
    fig_exp.update_layout(
        title="Portfolio Exposure Over Time",
        xaxis_title="Date",
        yaxis_title="Number of Positions",
        yaxis=dict(range=[0, 16]),
        height=300,
        template="plotly_dark"
    )

    # Stats summary
    stats_text = f"""
    ## Performance Metrics

    **Strategy Performance:**
    - Total Return: **{stats_tg[0]:.1f}%** ({stats_tg[0]-stats_spy[0]:+.1f}% vs SPY)
    - CAGR: **{stats_tg[3]:.1f}%** ({stats_tg[3]-stats_spy[3]:+.1f}% vs SPY)
    - Max Drawdown: **{stats_tg[2]:.1f}%** ({stats_tg[2]-stats_spy[2]:+.1f}% vs SPY)
    - Sharpe Ratio: **{stats_tg[3]/stats_tg[1]:.2f}** (if volatility > 0)

    **Current Holdings:** {len(current_slots)} positions
    """

    # Current holdings table
    if current_slots:
        holdings = []
        for t, entry_p, entry_d in current_slots:
            curr_p = engine.data_cache[t].iloc[-1]["Close"]
            perf = (curr_p - entry_p) / entry_p * 100
            holdings.append({
                "Ticker": t,
                "Entry Date": entry_d.strftime('%Y-%m-%d'),
                "Entry Price": f"${entry_p:.2f}",
                "Current Price": f"${curr_p:.2f}",
                "Performance": f"{perf:+.2f}%"
            })
        holdings_df = pd.DataFrame(holdings)
    else:
        holdings_df = pd.DataFrame({"Message": ["Portfolio is currently empty"]})

    return stats_text, fig, fig_exp, holdings_df

# === Tab 3: Ticker Analysis ===
def analyze_ticker(ticker, force_refresh=False):
    """Deep dive analysis for selected ticker"""
    if ticker not in engine.data_cache:
        return "Ticker not found in universe", None, None, pd.DataFrame()

    ticker_df = engine.data_cache[ticker].copy()
    ticker_df.index = pd.to_datetime(ticker_df.index)

    # Get theoretical trades
    ticker_trades = engine.get_ticker_trades(ticker, exit_strategy="trend_guardian")

    # Current status
    latest_row = ticker_df.iloc[-1]
    curr_price = latest_row["Close"]
    curr_stage = latest_row["Stage_Daily"]
    curr_score = latest_row["Score"]
    curr_atr = latest_row.get("ATR", 0)

    # Risk calculations
    risk_amt = 2 * curr_atr if curr_atr > 0 else curr_price * 0.05
    stop_loss = curr_price - risk_amt
    risk_pct = (risk_amt / curr_price) * 100
    target_amt = 3 * risk_amt
    target_price = curr_price + target_amt
    upside_pct = (target_amt / curr_price) * 100

    status_text = f"""
    ## {ticker} - Current Status

    - **Price:** ${curr_price:.2f}
    - **Stage:** {curr_stage}
    - **Score:** {curr_score:.1f}
    - **Stop Loss:** ${stop_loss:.2f} ({risk_pct:.2f}% risk)
    - **Profit Target:** ${target_price:.2f} (+{upside_pct:.2f}% upside)
    - **Trend Template:** {"✅ Pass" if latest_row.get("Trend_Template", False) else "❌ Fail"}
    """

    # Financial metrics
    ticker_metrics = metrics_engine.get_ticker_metrics(ticker, force_update=force_refresh)
    if ticker_metrics:
        metrics_text = f"""
        ### Financial Metrics
        - ROE: {ticker_metrics.get('ROE (%)', 'N/A')}%
        - Forward PER: {ticker_metrics.get('Forward PER', 'N/A')}
        - PEG Ratio: {ticker_metrics.get('PEG Ratio', 'N/A')}
        - Profit Margin: {ticker_metrics.get('Profit Margin (%)', 'N/A')}%
        - Debt to Equity: {ticker_metrics.get('Debt to Equity', 'N/A')}
        """
        status_text += metrics_text

    # Trade statistics
    if not ticker_trades.empty:
        avg_ret = ticker_trades["Return (%)"].mean()
        win_rate = (ticker_trades["Return (%)"] > 0).mean() * 100
        stats_text = f"""
        ### Historical Performance
        - Total Trades: {len(ticker_trades)}
        - Avg Return/Trade: {avg_ret:.2f}%
        - Win Rate: {win_rate:.1f}%
        """
        status_text += stats_text

    # Create price chart
    fig = go.Figure()

    # Price
    fig.add_trace(go.Scatter(
        x=ticker_df.index,
        y=ticker_df["Close"],
        name="Price",
        line=dict(color='white', width=2)
    ))

    # Moving averages
    fig.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["SMA200"], name="SMA 200", line=dict(color='blue', width=1, dash='dash')))
    fig.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["SMA50"], name="SMA 50", line=dict(color='red', width=1)))
    fig.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["SMA20"], name="SMA 20", line=dict(color='orange', width=1)))

    # Risk levels
    fig.add_hline(y=stop_loss, line_dash="dot", line_color="red", annotation_text="Stop Loss")
    fig.add_hline(y=target_price, line_dash="dot", line_color="green", annotation_text="Target 3:1")

    # Entry/exit markers
    if not ticker_trades.empty:
        entries = ticker_trades[["Entry_Date", "Entry_Price"]]
        exits = ticker_trades[["Exit_Date", "Exit_Price"]]

        fig.add_trace(go.Scatter(
            x=entries["Entry_Date"],
            y=entries["Entry_Price"],
            mode='markers',
            name='Buy Entry',
            marker=dict(symbol='triangle-up', size=12, color='lime')
        ))

        fig.add_trace(go.Scatter(
            x=exits["Exit_Date"],
            y=exits["Exit_Price"],
            mode='markers',
            name='Sell Exit',
            marker=dict(symbol='triangle-down', size=12, color='red')
        ))

    fig.update_layout(
        title=f"{ticker} Analysis",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        height=600,
        template="plotly_dark"
    )

    return status_text, fig, None, ticker_trades

# === Build Gradio Interface ===
with gr.Blocks(theme=gr.themes.Soft(primary_hue="green"), title="Trading Matrix") as demo:
    gr.Markdown("""
    # 📈 Trading Matrix - Trend Guardian Strategy
    **Data Coverage:** 2 years | **Universe:** ~400 tickers
    """)

    with gr.Tabs():
        # Tab 1: Live Scan
        with gr.Tab("🚀 Live Market Scan"):
            gr.Markdown("### Today's Top Candidates")
            scan_button = gr.Button("Run Live Scan", variant="primary", size="lg")

            top_ideas_header = gr.Markdown()
            top_ideas_table = gr.Dataframe(label="💎 TOP Ideas (Stage 2 + Score < 4 + QR > 85%)")
            buy_alerts_table = gr.Dataframe(label="🎯 Buy Alerts (Stage 2 + Score < 4)")
            universe_table = gr.Dataframe(label="📉 Full Universe Snapshot")

            scan_button.click(
                run_live_scan,
                outputs=[top_ideas_header, top_ideas_table, buy_alerts_table, universe_table]
            )

        # Tab 2: Backtest
        with gr.Tab("📊 Performance Hub"):
            gr.Markdown("### Strategy Backtest Results")
            exit_strategy = gr.Radio(
                choices=["trend_guardian", "sma_trailing", "score_decay"],
                value="trend_guardian",
                label="Exit Strategy"
            )
            backtest_button = gr.Button("Run Backtest", variant="primary")

            stats_output = gr.Markdown()
            equity_plot = gr.Plot(label="Equity Curve")
            exposure_plot = gr.Plot(label="Portfolio Exposure")
            holdings_table = gr.Dataframe(label="Current Holdings")

            backtest_button.click(
                run_backtest_analysis,
                inputs=[exit_strategy],
                outputs=[stats_output, equity_plot, exposure_plot, holdings_table]
            )

        # Tab 3: Ticker Analysis
        with gr.Tab("🔍 Ticker Deep Dive"):
            gr.Markdown("### Individual Stock Analysis")

            ticker_input = gr.Dropdown(
                choices=sorted(universe),
                label="Select Ticker",
                value="MSFT"
            )
            refresh_checkbox = gr.Checkbox(label="Force Refresh Fundamentals", value=False)
            analyze_button = gr.Button("Analyze Ticker", variant="primary")

            ticker_status = gr.Markdown()
            ticker_chart = gr.Plot(label="Price Chart with Signals")
            ticker_metrics = gr.Plot(visible=False)
            ticker_trades_table = gr.Dataframe(label="Theoretical Trade Log")

            analyze_button.click(
                analyze_ticker,
                inputs=[ticker_input, refresh_checkbox],
                outputs=[ticker_status, ticker_chart, ticker_metrics, ticker_trades_table]
            )

    gr.Markdown("""
    ---
    Made by **SFinance** | Not investment advice
    📧 Contact: sgseaux@gmail.com
    """)

if __name__ == "__main__":
    demo.launch(share=True, debug=True)
