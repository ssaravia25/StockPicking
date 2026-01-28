import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from ranking_backtest import RankingEngine, universe, get_stats
import yfinance as yf
from datetime import datetime
import logging

# Page config
st.set_page_config(page_title="Trading Matrix Dashboard", layout="wide")

# Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        text-align: center;
        border: 1px solid #e1e4e8;
    }
    .status-stage2 { color: #28a745; font-weight: bold; }
    .status-other { color: #6c757d; }
</style>
""", unsafe_allow_html=True)

@st.cache_resource
def get_engine():
    engine = RankingEngine(universe)
    engine.load_data()
    return engine

@st.cache_data
def run_backtest(_engine, exit_strategy):
    res_equity, res_trades = _engine.run(exit_strategy=exit_strategy)
    # The 'current' slots are those in engine.long_slots after run()
    # But since it's cached, we should return them
    return res_equity, res_trades, _engine.long_slots

@st.cache_data
def get_spy_data(start_date, end_date):
    spy = yf.Ticker("SPY").history(start=start_date, end=end_date)
    spy.index = spy.index.tz_localize(None)
    spy_daily = spy["Close"].pct_change().fillna(0)
    spy_equity = (1 + spy_daily).cumprod() * 100
    return pd.DataFrame({"Equity": spy_equity}, index=spy.index)

def calculate_annual_metrics(equity_df):
    """Calculate Return per year and YTD."""
    equity_df = equity_df.copy()
    equity_df.index = pd.to_datetime(equity_df.index)
    
    # Resample to year end
    annual = equity_df["Equity"].resample('YE').last()
    
    # Calculate returns
    years = []
    returns = []
    
    # First Year
    first_val = equity_df["Equity"].iloc[0]
    years.append(annual.index[0].year)
    returns.append(((annual.iloc[0] / first_val) - 1) * 100)
    
    # Subsequent Years
    for i in range(1, len(annual)):
        years.append(annual.index[i].year)
        ret = ((annual.iloc[i] / annual.iloc[i-1]) - 1) * 100
        returns.append(ret)
        
    df_returns = pd.DataFrame({"Year": years, "Return (%)": returns})
    return df_returns

# --- Sidebar ---
st.sidebar.title("🛡️ Trading Matrix")
st.sidebar.write("Strategy: **Trend Guardian**")
st.sidebar.markdown("---")
exit_strategy = st.sidebar.selectbox("Exit Strategy", ["trend_guardian", "sma_trailing", "score_decay"])

# Initialize Engine
engine = get_engine()

# --- Main App ---
st.title("📈 Trading Matrix Performance & Alerts")

tabs = st.tabs(["🚀 The Matrix (Live Alerts)", "📊 Performance Hub", "🔍 Trade Explorer", "📜 Strategy Rules"])

# Tab 1: Live Alerts
with tabs[0]:
    st.header("Today's Top Trend Guardian Candidates")
    
    if st.button("Run Live Scan"):
        with st.spinner("Scanning 80 tickers..."):
            results = []
            for t, df in engine.data_cache.items():
                last_row = df.iloc[-1]
                results.append({
                    "Ticker": t,
                    "Stage": last_row["Stage_Daily"],
                    "Score": round(last_row["Score"], 1),
                    "Price": round(last_row["Close"], 2),
                    "SMA10": round(last_row["SMA10"], 2),
                    "Trend Template": "✅ Pass" if last_row.get("Trend_Template", False) else "❌ Fail"
                })
            
            summary_df = pd.DataFrame(results)
            
            # Filter for Best (Stage 2 & Score < 4)
            best_candidates = summary_df[(summary_df["Stage"] == "Stage 2") & (summary_df["Score"] <= 4)].sort_values("Score")
            
            col1, col2 = st.columns([1, 1])
            with col1:
                st.subheader("🎯 Buy Alerts (Stage 2 + Score < 4)")
                st.dataframe(best_candidates, use_container_width=True)
            
            with col2:
                st.subheader("📉 Full Universe Snapshot")
                st.dataframe(summary_df.sort_values("Score"), use_container_width=True)

# Tab 2: Performance Hub
with tabs[1]:
    st.header(f"Backtest Results: {exit_strategy.replace('_', ' ').title()}")
    
    res, trade_log, current_slots = run_backtest(engine, exit_strategy)
    spy_res = get_spy_data(res.index[0], res.index[-1])
    
    # Stats
    stats_tg = get_stats(res)
    stats_spy = get_stats(spy_res)
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Return", f"{stats_tg[0]:.1f}%", f"{stats_tg[0]-stats_spy[0]:.1f}% vs SPY")
    c2.metric("CAGR", f"{stats_tg[3]:.1f}%", f"{stats_tg[3]-stats_spy[3]:.1f}% vs SPY")
    c3.metric("Max Drawdown", f"{stats_tg[2]:.1f}%", f"{stats_tg[2]-stats_spy[2]:.1f}% vs SPY", delta_color="inverse")
    c4.metric("Sharpe Ratio", f"{stats_tg[3]/stats_tg[1]:.2f}" if stats_tg[1] > 0 else "0.00")

    # Equity Curve Plot
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=res.index, y=res["Equity"], name="Strategy", line=dict(color='blue', width=3)))
    fig.add_trace(go.Scatter(x=spy_res.index, y=spy_res["Equity"], name="SPY Benchmark", line=dict(color='gray', dash='dash')))
    fig.update_layout(title="Equity Growth (Start = 100)", xaxis_title="Date", yaxis_title="Equity", height=500)
    st.plotly_chart(fig, use_container_width=True)

    # Exposure Chart
    st.subheader("🛡️ Portfolio Exposure (Active Slots)")
    fig_exp = go.Figure()
    fig_exp.add_trace(go.Scatter(
        x=res.index, 
        y=res["Long_Count"], 
        fill='tozeroy', 
        name="Active Slots", 
        line=dict(color='green', width=1)
    ))
    fig_exp.update_layout(
        title="Number of Active Positions over Time",
        xaxis_title="Date",
        yaxis_title="Positions",
        yaxis=dict(range=[0, 11]),
        height=300
    )
    st.plotly_chart(fig_exp, use_container_width=True)
    st.caption("When exposure is low (e.g., < 5 slots), a large gain in one stock has a smaller impact on total equity (Cash Drag).")

    # Current Portfolio View
    st.subheader("🛡️ Current Portfolio Holdings")
    if current_slots:
        holdings = []
        for t, entry_p, entry_d in current_slots:
            curr_p = engine.data_cache[t].iloc[-1]["Close"]
            perf = (curr_p - entry_p) / entry_p * 100
            holdings.append({
                "Ticker": t,
                "Entry Date": entry_d.strftime('%Y-%m-%d'),
                "Entry Price": entry_p,
                "Current Price": curr_p,
                "Performance (%)": perf
            })
        st.dataframe(pd.DataFrame(holdings).style.format({
            "Entry Price": "${:.2f}",
            "Current Price": "${:.2f}",
            "Performance (%)": "{:+.2f}%"
        }), use_container_width=True)
    else:
        st.write("Portfolio is currently empty.")

    # Annual Returns
    st.subheader("Annual Performance Breakdown")
    col_ann1, col_ann2 = st.columns(2)
    
    with col_ann1:
        ann_stats = calculate_annual_metrics(res)
        st.write("**Strategy Returns by Year**")
        st.dataframe(ann_stats.style.format({"Return (%)": "{:.2f}%"}), use_container_width=True)
    
    with col_ann2:
        spy_ann = calculate_annual_metrics(spy_res)
        st.write("**SPY Returns by Year**")
        st.dataframe(spy_ann.style.format({"Return (%)": "{:.2f}%"}), use_container_width=True)

# Tab 3: Trade Explorer
with tabs[2]:
    st.header("Deep Dive: Theoretical Strategy Potential")
    st.write("This view shows every window where the strategy rules were met, ignoring portfolio limits.")
    
    # Select from full universe
    selected_ticker = st.selectbox("Select a Ticker to analyze", sorted(universe))
    
    ticker_trades = engine.get_ticker_trades(selected_ticker, exit_strategy=exit_strategy)
    
    if selected_ticker in engine.data_cache:
        ticker_df = engine.data_cache[selected_ticker].copy()
        ticker_df.index = pd.to_datetime(ticker_df.index)
        
        if not ticker_trades.empty:
            ticker_trades = ticker_trades.sort_values("Entry_Date")
            avg_ret = ticker_trades["Return (%)"].mean()
            win_rate = (ticker_trades["Return (%)"] > 0).mean() * 100
            
            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("Total Trades", len(ticker_trades))
            col_t2.metric("Avg Return/Trade", f"{avg_ret:.2f}%")
            col_t3.metric("Win Rate", f"{win_rate:.1f}%")
        else:
            st.info("No theoretical trades found for this ticker using the current strategy.")

        # --- Current Logic Summary Box ---
        latest_row = ticker_df.iloc[-1]
        curr_price = latest_row["Close"]
        curr_stage = latest_row["Stage_Daily"]
        curr_score = latest_row["Score"]
        curr_atr = latest_row.get("ATR", 0)
        
        # Risk Logic: Stop = -2 ATR, Target = 3x Risk
        risk_amt = 2 * curr_atr if curr_atr > 0 else curr_price * 0.05
        stop_loss = curr_price - risk_amt
        risk_pct = (risk_amt / curr_price) * 100
        target_amt = 3 * risk_amt
        target_price = curr_price + target_amt
        upside_pct = (target_amt / curr_price) * 100

        st.markdown(f"""
        <div style="background-color: #1e1e1e; padding: 15px; border-radius: 5px; border-left: 5px solid #00ff00; margin-bottom: 20px;">
            <p style="margin: 0; color: #888;">✓ Lógica Aplicada a {selected_ticker}:</p>
            <p style="margin: 0; font-weight: bold; font-size: 1.1em;">- Precio actual: ${curr_price:.2f}</p>
            <p style="margin: 0;">- Stop Loss (Riesgo): ${stop_loss:.2f} ({risk_pct:.2f}% del precio)</p>
            <p style="margin: 0;">- Profit Target (3x Riesgo): ${target_price:.2f} (+{upside_pct:.2f}% de subida)</p>
            <p style="margin: 0;">- Stage: {curr_stage}</p>
            <p style="margin: 0;">- Score: {curr_score}</p>
            <p style="margin: 0;">- Trend Template: {"✅ Pass" if latest_row.get("Trend_Template", False) else "❌ Fail"}</p>
        </div>
        """, unsafe_allow_html=True)

        # --- Modernized Professional Chart ---
        fig_t = go.Figure()

        # Custom Styling Constants
        COLORS = {
            "price": "#E0E0E0",
            "sma10": "#2E7D32", # Green
            "sma20": "#F9A825", # Amber
            "sma50": "#C62828", # Red
            "sma200": "#1565C0", # Blue
            "volume": "rgba(100, 100, 100, 0.2)",
            "stage": {
                "Stage 2": "rgba(0, 255, 0, 0.05)",
                "Stage 4": "rgba(255, 0, 0, 0.05)",
                "Stage 3": "rgba(255, 255, 0, 0.05)"
            }
        }

        # Stage Shading
        for stage_name, color in COLORS["stage"].items():
            mask = ticker_df["Stage_Daily"] == stage_name
            if mask.any():
                intervals = []
                start = None
                for d, val in mask.items():
                    if val and start is None: start = d
                    elif not val and start is not None:
                        intervals.append((start, d))
                        start = None
                if start is not None: intervals.append((start, mask.index[-1]))
                
                for start, end in intervals:
                    fig_t.add_vrect(x0=start, x1=end, fillcolor=color, layer="below", line_width=0)

        # Volume (Secondary Axis)
        fig_t.add_trace(go.Bar(
            x=ticker_df.index, y=ticker_df["Volume"],
            name="Volume", marker_color=COLORS["volume"],
            yaxis="y2", opacity=0.3
        ))

        # Moving Averages
        fig_t.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["SMA200"], name="SMA 200", line=dict(color=COLORS["sma200"], width=1.5, dash='dash')))
        fig_t.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["SMA50"], name="SMA 50", line=dict(color=COLORS["sma50"], width=1.2)))
        fig_t.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["SMA20"], name="SMA 20", line=dict(color=COLORS["sma20"], width=1)))
        
        # Price Curve
        fig_t.add_trace(go.Scatter(x=ticker_df.index, y=ticker_df["Close"], name="Price", line=dict(color=COLORS["price"], width=2.5)))

        # Risk Lines
        fig_t.add_hline(y=stop_loss, line_dash="dot", line_color="#FF5252", opacity=0.8, annotation_text="Risk Level", annotation_position="bottom right")
        fig_t.add_hline(y=target_price, line_dash="dot", line_color="#69F0AE", opacity=0.8, annotation_text="Target 3:1", annotation_position="top right")

        # Highlights for Entry/Exit
        if not ticker_trades.empty:
            entries = ticker_trades[["Entry_Date", "Entry_Price"]]
            exits = ticker_trades[["Exit_Date", "Exit_Price"]]
            
            fig_t.add_trace(go.Scatter(
                x=entries["Entry_Date"], y=entries["Entry_Price"],
                mode='markers', name='Buy Entry',
                marker=dict(symbol='triangle-up', size=14, color='#00E676', line=dict(width=1, color='white'))
            ))
            
            fig_t.add_trace(go.Scatter(
                x=exits["Exit_Date"], y=exits["Exit_Price"],
                mode='markers', name='Sell Exit',
                marker=dict(symbol='triangle-down', size=14, color='#FF1744', line=dict(width=1, color='white'))
            ))
        
        fig_t.update_layout(
            title=dict(text=f"<b>{selected_ticker}</b> Analysis Hub", font=dict(size=24, color='white')),
            xaxis_title="Date",
            yaxis_title="Price ($)",
            yaxis2=dict(title="Volume", overlaying="y", side="right", showgrid=False, visible=False),
            height=700,
            template="plotly_dark",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=50, r=50, t=80, b=50),
            hovermode="x unified",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)'
        )
        st.plotly_chart(fig_t, use_container_width=True)
        
        # Trades Table
        if not ticker_trades.empty:
            st.subheader("📊 Performance: Theoretical Trade Logs")
            st.dataframe(ticker_trades.style.format({
                "Entry_Price": "${:.2f}",
                "Exit_Price": "${:.2f}",
                "Return (%)": "{:+.2f}%",
                "Entry_Date": lambda x: x.strftime('%Y-%m-%d'),
                "Exit_Date": lambda x: x.strftime('%Y-%m-%d')
            }).background_gradient(subset=["Return (%)"], cmap="RdYlGn", vmin=-15, vmax=15), use_container_width=True)
    else:
        st.warning(f"Data for {selected_ticker} not loaded correctly. Try refreshing.")

# Tab 4: Strategy Rules
with tabs[3]:
    st.header("The Matrix Logic")
    col_rule1, col_rule2 = st.columns(2)
    
    with col_rule1:
        st.subheader("🛡️ Long Entry Rules")
        st.write("""
        1. **Stage 2 Filter**: Stock must be in Stage 2 (Advancing Phase).
        2. **Trend Template**: Pass all 8 Minervini Trend Rules.
        3. **Score < 4**: Price must be within ~4% of the 10-day Moving Average (Low-risk pullback).
        """)
        
    with col_rule2:
        st.subheader("🚪 Exit Rules")
        st.write(f"""
        **Selected Strategy: {exit_strategy}**
        - **Trend Guardian**: Exit only if Stage 2 breaks (Stock moves to Stage 3 or 4).
        - **SMA Trailing**: Exit if Price closes below 20-day SMA.
        - **Score Decay**: Exit if momentum stalls after a peak (Score > 8 + price weakness).
        """)

st.markdown("---")
st.markdown("""
<div style="text-align: center; color: #888; font-size: 0.8em; padding: 20px;">
    Hecho por <b>SFinance</b>, not investment advise.<br>
    Any suggestions? mail to: <a href="mailto:sgseaux@gmail.com" style="color: #00ff00;">sgseaux@gmail.com</a><br>
    <span style="font-size: 0.7em; opacity: 0.6;">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Data coverage: 2018-2026</span>
</div>
""", unsafe_allow_html=True)
