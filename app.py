import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.stock_data import (
    get_stock_data, get_ticker_info, get_financials, get_news, POPULAR_STOCKS,
    get_intraday_price,
)
from services.technical import (
    calculate_indicators, get_technical_signals, calculate_technical_score,
    calculate_horizon_scores, analyze_volume_price,
)
from services.fundamental import analyze_fundamentals, calculate_fundamental_score
from services.news import get_all_news, calculate_news_sentiment_score, get_catalysts
from services.recommendation import (
    generate_recommendation, generate_timeframe_recommendations, build_rationale,
    _action_for as _action_for_score,
)
from services.margin import get_margin_data, get_margin_trend, calculate_margin_signal
from services.stock_lookup import resolve_query, display_name, resolve_company_name
from services.potential import calculate_potential_score
from services.universe import scan_universe, get_listed_snapshot
from services.evidence import (
    get_stats as ev_stats, verdict as ev_verdict, all_model_rows as ev_rows,
    benchmark_return as ev_bench, meta as ev_meta,
    regime_stats as ev_regime_stats, best_strategies_for_regime as ev_best_for_regime,
    get_stats_for_model as ev_stats_model, regime_stats_for_model as ev_regime_model,
    score_bucket_stats as ev_bucket, buy_threshold as ev_threshold,
    horizon_efficacy as ev_horizon_efficacy,
    MODEL_TO_STRATEGY,
)
from services.sector import analyse_sectors, get_industry_map, industry_name
from services.strategies import STRATEGIES, LABELS as STRAT_LABELS, \
    CAPTIONS as STRAT_CAPTIONS, get as get_strategy, \
    select as strat_select, explain as strat_explain
from services.ui import (
    overheat_badge,
    pe_badge, pe_inline, horizon_cells, evidence_badge, rr_cell,
    threshold_note, long_threshold,
)
from services.portfolio import (
    load_holdings, upsert_holding, remove_holding, update_holding_name,
    get_holding, compute_position, portfolio_totals,
)
from services.market import get_market_regime, get_index_forward_return
from services.technical import calculate_risk_plan
from services.analysis import prepare_frame, compute_scores, PERIOD_ROWS

# 趨勢結構分是「橫斷面百分位」，所以買進線可以直接用百分位解讀：
# 70 = 只看贏過全市場七成的股票。回測分桶顯示 70 以下的區間超額報酬為負。
TREND_BUY_BAR = 70.0
from services.target_price import calculate_target_price
from services.analyst_targets import get_analyst_targets
from services.catalyst_impact import get_catalyst_impact
from services.article_fetch import fetch_article_excerpt
from services.limit_up import get_limit_up_stocks
from services.watchlist import (
    load_watchlist, is_in_watchlist, add_to_watchlist,
    remove_from_watchlist, update_watchlist_name,
)

st.set_page_config(
    page_title="台灣股票分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .block-container { padding-top: 1rem; }
  .news-card { border-left: 4px solid #444; padding: 10px 14px; margin: 8px 0;
               border-radius: 4px; background: #1e2130; }
  .news-pos { border-left-color: #4caf50 !important; }
  .news-neg { border-left-color: #f44336 !important; }
  .news-neu { border-left-color: #78909c !important; }
  .stock-row { background: #1e2130; border-radius: 10px; padding: 12px 16px;
               margin: 6px 0; border: 1px solid #2d3548; }
  .rank-badge { font-size: 22px; font-weight: 900; width: 36px; text-align: center; }
</style>
""", unsafe_allow_html=True)


# ─── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar():
    with st.sidebar:
        st.markdown("## 📈 台灣股票分析")

        page = st.radio(
            "功能選單",
            ["📊 個股分析", "🎯 智能選股", "💼 我的持股"],
            key="main_page",
            horizontal=False,
        )

        st.markdown("---")

        if page == "📊 個股分析":
            watchlist = load_watchlist()
            if watchlist:
                st.markdown("#### ⭐ 自選股")
                for item in watchlist:
                    wcol1, wcol2 = st.columns([4, 1])
                    with wcol1:
                        if st.button(f"{item['stock_id']} {item['name']}",
                                     key=f"wl_go_{item['stock_id']}", use_container_width=True):
                            st.session_state["stock_id"] = item["stock_id"]
                    with wcol2:
                        if st.button("✕", key=f"wl_del_{item['stock_id']}", use_container_width=True):
                            remove_from_watchlist(item["stock_id"])
                            st.rerun()
                st.markdown("---")

            st.markdown("#### 快速選股")
            quick_picks = [
                ("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"),
                ("2412", "中華電"), ("2882", "國泰金"), ("2303", "聯電"),
                ("2308", "台達電"), ("2603", "長榮"), ("8039", "台虹"),
            ]
            cols = st.columns(2)
            for i, (code, name) in enumerate(quick_picks):
                with cols[i % 2]:
                    if st.button(f"{code}\n{name}", key=f"btn_{code}", use_container_width=True):
                        st.session_state["stock_id"] = code

            st.markdown("---")
            raw_query = st.text_input(
                "輸入股票代碼或中文名稱",
                value=st.session_state.get("stock_id", "2330"),
                placeholder="例如: 2330 或 台積電",
            )
            stock_id = st.session_state.get("stock_id", "2330")
            if raw_query and raw_query.strip() != stock_id:
                res = resolve_query(raw_query)
                if res["matched"]:
                    stock_id = res["stock_id"]
                    if not res["is_code"]:
                        st.caption(f"🔎 已對應「{raw_query.strip()}」→ {stock_id} {res['name']}")
                else:
                    st.warning(f"查無「{raw_query.strip()}」，請確認代碼或名稱是否正確")
            elif raw_query:
                stock_id = raw_query.strip()
            st.session_state["stock_id"] = stock_id

            if stock_id:
                current_name = display_name(stock_id)
                if is_in_watchlist(stock_id):
                    if st.button("★ 移除自選股", key="wl_toggle", use_container_width=True):
                        remove_from_watchlist(stock_id)
                        st.rerun()
                else:
                    if st.button("☆ 加入自選股", key="wl_toggle", use_container_width=True):
                        add_to_watchlist(stock_id, current_name)
                        st.rerun()
        else:
            stock_id = st.session_state.get("stock_id", "2330")

        period_map = {
            "3 個月": "3mo", "6 個月": "6mo",
            "1 年": "1y", "2 年": "2y", "3 年": "3y",
        }
        period_label = st.selectbox("分析期間", list(period_map.keys()), index=2)
        period = period_map[period_label]

        # ── Backtest: analysis as-of a past date ──────────────────────────────
        today = datetime.date.today()
        as_of_date = today
        if page == "📊 個股分析":
            st.markdown("---")
            st.markdown("#### 📅 分析基準日（可回測）")
            as_of_date = st.date_input(
                "選擇過去日期，即可看「當天」的買賣建議並事後驗證",
                min_value=today - datetime.timedelta(days=1095),
                max_value=today,
                key="as_of_date",
            )
            if as_of_date < today:
                st.info(f"🕐 回測模式：分析還原至 **{as_of_date}**（含）以前的資料")

        st.markdown("---")
        st.markdown("""
**資料來源**
- 股價: Yahoo Finance
- 新聞: Yahoo Finance + Google News

⚠️ **免責聲明**
本系統僅供參考，不構成投資建議。
        """)

    return stock_id, period, page, as_of_date


# ─── Technical Tab ────────────────────────────────────────────────────────────

def render_technical_tab(df: pd.DataFrame, stock_id: str, company_name: str):
    st.subheader(f"技術分析 — {company_name} ({stock_id})")

    col_left, col_right = st.columns([4, 1])
    with col_right:
        show_ma = st.multiselect(
            "均線", ["MA5", "MA10", "MA20", "MA60", "MA120"],
            default=["MA20", "MA60"], key="ta_ma",
        )
        show_bb = st.checkbox("布林通道", value=True, key="ta_bb")
        show_vol = st.checkbox("成交量", value=True, key="ta_vol")

    rows = 1 + int(show_vol) + 1 + 1
    row_heights = [0.55]
    if show_vol:
        row_heights.append(0.12)
    row_heights += [0.17, 0.16]
    subplot_titles = [f"{company_name} K線圖"]
    if show_vol:
        subplot_titles.append("成交量")
    subplot_titles += ["MACD", "RSI (14)"]

    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.025,
        subplot_titles=subplot_titles,
        row_heights=row_heights,
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="K線",
        increasing_line_color="#f03e3e", decreasing_line_color="#2f9e44",
        increasing_fillcolor="#f03e3e", decreasing_fillcolor="#2f9e44",
    ), row=1, col=1)

    ma_colors = {
        "MA5": "#ffd43b", "MA10": "#ff922b",
        "MA20": "#74c0fc", "MA60": "#e599f7", "MA120": "#a9e34b",
    }
    for ma in show_ma:
        if ma in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[ma], name=ma,
                line=dict(color=ma_colors[ma], width=1.4),
            ), row=1, col=1)

    if show_bb and "BB_upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_upper"], name="BB上軌",
            line=dict(color="rgba(150,150,150,0.7)", width=1, dash="dot"),
            showlegend=False,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["BB_lower"], name="BB下軌",
            line=dict(color="rgba(150,150,150,0.7)", width=1, dash="dot"),
            fill="tonexty", fillcolor="rgba(100,100,100,0.08)",
            showlegend=False,
        ), row=1, col=1)

    vol_row = 2 if show_vol else None
    if show_vol:
        vol_colors = ["#f03e3e" if c >= o else "#2f9e44"
                      for c, o in zip(df["Close"], df["Open"])]
        fig.add_trace(go.Bar(
            x=df.index, y=df["Volume"], name="成交量",
            marker_color=vol_colors, opacity=0.7,
        ), row=vol_row, col=1)
        if "Vol_MA5" in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df["Vol_MA5"], name="量MA5",
                line=dict(color="#ffd43b", width=1),
            ), row=vol_row, col=1)
        if "Vol_MA20" in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df["Vol_MA20"], name="量MA20",
                line=dict(color="#e599f7", width=1),
            ), row=vol_row, col=1)
        if "Vol_MA60" in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df["Vol_MA60"], name="量MA60",
                line=dict(color="#74c0fc", width=1),
            ), row=vol_row, col=1)

    macd_row = (vol_row or 1) + 1
    rsi_row = macd_row + 1

    if "MACD" in df.columns:
        hist_colors = ["#f03e3e" if v >= 0 else "#2f9e44" for v in df["MACD_hist"]]
        fig.add_trace(go.Bar(
            x=df.index, y=df["MACD_hist"], name="MACD柱",
            marker_color=hist_colors, opacity=0.6,
        ), row=macd_row, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MACD"], name="MACD",
            line=dict(color="#74c0fc", width=1.3),
        ), row=macd_row, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["MACD_signal"], name="Signal",
            line=dict(color="#ff922b", width=1.3),
        ), row=macd_row, col=1)

    if "RSI" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["RSI"], name="RSI",
            line=dict(color="#e599f7", width=1.5),
        ), row=rsi_row, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="rgba(240,62,62,0.5)", row=rsi_row, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="rgba(47,158,68,0.5)", row=rsi_row, col=1)
        fig.add_hrect(y0=70, y1=100, fillcolor="rgba(240,62,62,0.05)", row=rsi_row, col=1, line_width=0)
        fig.add_hrect(y0=0, y1=30, fillcolor="rgba(47,158,68,0.05)", row=rsi_row, col=1, line_width=0)

    fig.update_layout(
        height=750, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
        xaxis_rangeslider_visible=False,
        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
        font=dict(color="#fafafa"),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    for i in range(1, rows + 1):
        fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.06)", row=i, col=1)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.06)", row=i, col=1)

    with col_left:
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("技術訊號一覽")
    signals = get_technical_signals(df)
    if signals:
        sig_cols = st.columns(len(signals))
        sig_style = {
            "bullish": ("#2f9e44", "#0a3d1c", "🟢"),
            "bearish": ("#f03e3e", "#3d0a0a", "🔴"),
            "neutral": ("#78909c", "#1a2029", "⚪"),
        }
        for i, (name, sig, val, desc) in enumerate(signals):
            color, bg, icon = sig_style[sig]
            with sig_cols[i]:
                st.markdown(f"""
<div style="background:{bg};border:1px solid {color};border-radius:10px;
            padding:12px 8px;text-align:center;height:110px;">
  <div style="font-size:22px">{icon}</div>
  <div style="font-weight:700;font-size:13px">{name}</div>
  <div style="color:{color};font-weight:600">{val}</div>
  <div style="font-size:11px;color:#aaa;margin-top:4px">{desc}</div>
</div>""", unsafe_allow_html=True)


# ─── News Tab ─────────────────────────────────────────────────────────────────

def _fmt_pubdate(pub) -> str:
    if isinstance(pub, (int, float)) and pub > 0:
        return datetime.datetime.fromtimestamp(pub).strftime("%Y/%m/%d %H:%M")
    if isinstance(pub, str) and pub:
        return pub[:30]
    return ""


def render_news_tab(stock_id: str, company_name: str):
    st.subheader(f"消息面 — {company_name} ({stock_id})")
    st.caption(
        "消息依類型分類評分：「技術突破」+30分、「訂單大增」+22分、「訴訟風險」-28分等，"
        "遠比單純計算正負面關鍵字更能反映新聞的實際影響。"
    )

    with st.spinner("載入並分析新聞中..."):
        yf_news = get_news(stock_id)
        all_news = get_all_news(stock_id, company_name, yf_news)
        news_score, reasons = calculate_news_sentiment_score(all_news)
        catalysts = get_catalysts(all_news)

    if not all_news:
        st.info("目前無法取得相關新聞，請稍後再試。")
        return

    # ── Summary metrics ───────────────────────────────────────────────────────
    pos_count = sum(1 for n in all_news if n["sentiment"] == "positive")
    neg_count = sum(1 for n in all_news if n["sentiment"] == "negative")
    neu_count = len(all_news) - pos_count - neg_count
    catalyst_pos = len(catalysts["positive"])
    catalyst_neg = len(catalysts["negative"])

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("正面消息（篇）", pos_count)
    with col2:
        st.metric("負面消息（篇）", neg_count)
    with col3:
        st.metric("中性消息（篇）", neu_count)
    with col4:
        st.metric("消息面評分", news_score)

    # Sentiment bar
    total = len(all_news)
    pos_pct = pos_count / total * 100
    neg_pct = neg_count / total * 100
    neu_pct = 100 - pos_pct - neg_pct
    st.markdown(f"""
<div style="display:flex;height:16px;border-radius:8px;overflow:hidden;margin:10px 0 4px 0;">
  <div style="width:{pos_pct:.0f}%;background:#2f9e44;"></div>
  <div style="width:{neu_pct:.0f}%;background:#78909c;"></div>
  <div style="width:{neg_pct:.0f}%;background:#f03e3e;"></div>
</div>
<div style="display:flex;gap:16px;font-size:12px;color:#aaa;margin-bottom:8px;">
  <span>🟢 正面 {pos_pct:.0f}%</span>
  <span>⚪ 中性 {neu_pct:.0f}%</span>
  <span>🔴 負面 {neg_pct:.0f}%</span>
</div>""", unsafe_allow_html=True)

    # ── Catalyst section ──────────────────────────────────────────────────────
    if catalyst_pos > 0 or catalyst_neg > 0:
        st.markdown("---")
        st.markdown("#### 🔍 潛在尚未完全反映的催化劑")
        st.caption(
            "這些是「有特定業務影響的類型」新聞——技術突破、新訂單、法律風險等。"
            "股價對它們的消化通常需要數週到數月，可能仍有上漲或下跌空間。"
        )

        if catalysts["positive"]:
            st.markdown("**📈 潛在利多催化劑**")
            shown_impacts = set()
            for n in catalysts["positive"]:
                cat = n["category"]
                cat_key = n["category_key"]
                link = n.get("link") or "#"
                pub_str = _fmt_pubdate(n.get("pub_date", ""))
                st.markdown(f"""
<div style="background:#0a2d1c;border:1px solid {cat['color']};border-radius:8px;
            padding:12px 16px;margin:6px 0;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
    <span style="font-size:18px">{cat['emoji']}</span>
    <span style="font-size:13px;font-weight:700;color:{cat['color']};
                 border:1px solid {cat['color']};border-radius:4px;padding:2px 8px;">
      {cat['name']}  +{n['score_impact']}分
    </span>
    <span style="font-size:12px;color:#aaa;">{n.get('source','')} · {pub_str}</span>
  </div>
  <a href="{link}" target="_blank"
     style="color:#90caf9;text-decoration:none;font-size:14px;font-weight:500;">
    {n['title']}
  </a>
</div>""", unsafe_allow_html=True)

                # Article excerpt (fetched lazily, cached)
                if link and link != "#":
                    _exc = fetch_article_excerpt(link)
                    if _exc:
                        st.markdown(
                            f"<div style='font-size:12px;color:#b0bec5;margin:-4px 0 4px 0;"
                            f"padding:6px 10px;background:#0d1f14;border-radius:4px;"
                            f"border-left:2px solid #4caf50;'>"
                            f"📄 {_exc}</div>",
                            unsafe_allow_html=True,
                        )

                # Show impact estimate once per category type
                if cat_key not in shown_impacts:
                    shown_impacts.add(cat_key)
                    impact = get_catalyst_impact(cat_key)
                    if impact:
                        with st.expander(
                            f"📐 {impact['emoji']} 「{impact['title']}」類型的潛在營運影響估算",
                            expanded=False,
                        ):
                            st.caption("⚠️ 以下為行業慣例的方向性估算，非該新聞的確切數字，請以原文內容為準。")
                            icols = st.columns(2)
                            with icols[0]:
                                st.markdown(f"**股價反應（歷史慣例）**")
                                st.info(impact["stock_reaction"])
                                st.markdown(f"**潛在營收影響**")
                                st.info(impact["revenue_impact"])
                                st.markdown(f"**反映時間軸**")
                                st.info(impact["revenue_horizon"])
                            with icols[1]:
                                st.markdown(f"**毛利率影響**")
                                st.info(impact["margin_impact"])
                                st.markdown(f"**主要風險**")
                                st.warning(impact["risk_note"])
                                st.markdown(f"**估算信心度**")
                                st.caption(impact["confidence"])

        if catalysts["negative"]:
            st.markdown("**📉 潛在利空催化劑**")
            shown_impacts_neg = set()
            for n in catalysts["negative"]:
                cat = n["category"]
                cat_key = n["category_key"]
                link = n.get("link") or "#"
                pub_str = _fmt_pubdate(n.get("pub_date", ""))
                st.markdown(f"""
<div style="background:#2d0a0a;border:1px solid {cat['color']};border-radius:8px;
            padding:12px 16px;margin:6px 0;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
    <span style="font-size:18px">{cat['emoji']}</span>
    <span style="font-size:13px;font-weight:700;color:{cat['color']};
                 border:1px solid {cat['color']};border-radius:4px;padding:2px 8px;">
      {cat['name']}  {n['score_impact']}分
    </span>
    <span style="font-size:12px;color:#aaa;">{n.get('source','')} · {pub_str}</span>
  </div>
  <a href="{link}" target="_blank"
     style="color:#90caf9;text-decoration:none;font-size:14px;font-weight:500;">
    {n['title']}
  </a>
</div>""", unsafe_allow_html=True)

                # Article excerpt (fetched lazily, cached)
                if link and link != "#":
                    _exc_neg = fetch_article_excerpt(link)
                    if _exc_neg:
                        st.markdown(
                            f"<div style='font-size:12px;color:#b0bec5;margin:-4px 0 4px 0;"
                            f"padding:6px 10px;background:#1f0d0d;border-radius:4px;"
                            f"border-left:2px solid #f44336;'>"
                            f"📄 {_exc_neg}</div>",
                            unsafe_allow_html=True,
                        )

                if cat_key not in shown_impacts_neg:
                    shown_impacts_neg.add(cat_key)
                    impact = get_catalyst_impact(cat_key)
                    if impact:
                        with st.expander(
                            f"📐 {impact['emoji']} 「{impact['title']}」類型的潛在營運影響估算",
                            expanded=False,
                        ):
                            st.caption("⚠️ 以下為行業慣例的方向性估算，非該新聞的確切數字。")
                            icols = st.columns(2)
                            with icols[0]:
                                st.markdown("**股價反應（歷史慣例）**")
                                st.error(impact["stock_reaction"])
                                st.markdown("**潛在營收影響**")
                                st.error(impact["revenue_impact"])
                            with icols[1]:
                                st.markdown("**主要風險**")
                                st.warning(impact["risk_note"])
                                st.markdown("**估算信心度**")
                                st.caption(impact["confidence"])

    # ── All news list ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 所有新聞")

    _filter_key = f"news_filter_{stock_id}"
    # Reset to default when switching stocks (key changes → session_state entry absent)
    if _filter_key not in st.session_state:
        st.session_state[_filter_key] = ["positive", "negative", "neutral"]

    fcol1, fcol2 = st.columns([5, 1])
    with fcol1:
        filter_sent = st.multiselect(
            "篩選情緒",
            ["positive", "negative", "neutral"],
            default=["positive", "negative", "neutral"],
            format_func=lambda x: {"positive": "正面", "negative": "負面", "neutral": "中性"}[x],
            key=_filter_key,
        )
    with fcol2:
        if st.button("重置", key=f"news_filter_reset_{stock_id}"):
            st.session_state[_filter_key] = ["positive", "negative", "neutral"]
            st.rerun()

    if not filter_sent:
        st.info("請選擇至少一種情緒類別。")

    border_colors = {"positive": "#2f9e44", "negative": "#f03e3e", "neutral": "#78909c"}

    for n in [x for x in all_news if x["sentiment"] in filter_sent]:
        link = n.get("link") or "#"
        title = n.get("title", "")
        source = n.get("source", "")
        pub_str = _fmt_pubdate(n.get("pub_date", ""))
        sentiment = n["sentiment"]
        border = border_colors[sentiment]
        cat = n.get("category")

        cat_badge = ""
        if cat:
            cat_badge = (
                f'<span style="font-size:11px;font-weight:600;color:{cat["color"]};'
                f'border:1px solid {cat["color"]};border-radius:3px;padding:1px 6px;margin-left:8px;">'
                f'{cat["emoji"]} {cat["name"]}</span>'
            )

        impact = n.get("score_impact", 0)
        impact_str = f"+{impact}" if impact > 0 else str(impact)
        impact_color = "#4caf50" if impact > 0 else "#f44336" if impact < 0 else "#aaa"
        impact_badge = (
            f'<span style="font-size:11px;color:{impact_color};margin-left:6px;">'
            f'({impact_str}分)</span>'
        ) if impact != 0 else ""

        st.markdown(f"""
<div class="news-card" style="border-left-color:{border};">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:4px;">
    <span style="font-size:12px;color:#aaa;">{source} &nbsp;|&nbsp; {pub_str}</span>
    <span>{cat_badge}{impact_badge}</span>
  </div>
  <a href="{link}" target="_blank"
     style="color:#90caf9;text-decoration:none;font-size:15px;font-weight:500;">
    {title}
  </a>
</div>""", unsafe_allow_html=True)


# ─── Fundamental Tab ──────────────────────────────────────────────────────────

def render_fundamental_tab(stock_id: str, info: dict, financials: dict):
    st.subheader(f"基本面分析 — {resolve_company_name(stock_id, info)} ({stock_id})")

    fundamentals = analyze_fundamentals(info, financials)

    st.markdown("#### 關鍵財務指標")
    cols = st.columns(4)
    metrics = [
        ("本益比 P/E", fundamentals.get("pe_ratio"), "{:.1f}"),
        ("股價淨值比 P/B", fundamentals.get("pb_ratio"), "{:.2f}"),
        ("ROE", fundamentals.get("roe"), "{:.1%}"),
        ("ROA", fundamentals.get("roa"), "{:.1%}"),
        ("淨利率", fundamentals.get("profit_margin"), "{:.1%}"),
        ("營業利益率", fundamentals.get("operating_margin"), "{:.1%}"),
        ("殖利率", fundamentals.get("dividend_yield"), "{:.2%}"),
        ("Beta", fundamentals.get("beta"), "{:.2f}"),
        ("EPS", fundamentals.get("eps"), "{:.2f}"),
        ("負債/股東權益", fundamentals.get("debt_to_equity"), "{:.0f}"),
        ("流動比率", fundamentals.get("current_ratio"), "{:.2f}"),
        ("營收年增率", fundamentals.get("revenue_growth"), "{:.1%}"),
    ]
    for i, (label, value, fmt) in enumerate(metrics):
        with cols[i % 4]:
            display = fmt.format(value) if value is not None else "N/A"
            st.metric(label, display)

    st.markdown("---")

    rev_hist = fundamentals.get("revenue_history")
    net_hist = fundamentals.get("net_income_history")

    if rev_hist is not None and not rev_hist.empty:
        st.markdown("#### 年度營收與淨利趨勢")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[str(d.year) for d in rev_hist.index],
            y=rev_hist.values / 1e9,
            name="營收 (十億)", marker_color="#74c0fc",
        ))
        if net_hist is not None and not net_hist.empty:
            fig.add_trace(go.Bar(
                x=[str(d.year) for d in net_hist.index],
                y=net_hist.values / 1e9,
                name="淨利 (十億)", marker_color="#a9e34b",
            ))
        fig.update_layout(
            height=350, barmode="group",
            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
            font=dict(color="#fafafa"), yaxis_title="新台幣 (十億元)",
            legend=dict(orientation="h"), margin=dict(l=10, r=10, t=30, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    q_rev = fundamentals.get("quarterly_revenue")
    if q_rev is not None and not q_rev.empty:
        st.markdown("#### 季度營收趨勢")
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=[str(d)[:7] for d in q_rev.index],
            y=q_rev.values / 1e9,
            name="季營收", marker_color="#e599f7",
        ))
        fig2.update_layout(
            height=280, plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
            font=dict(color="#fafafa"), yaxis_title="新台幣 (十億元)",
            margin=dict(l=10, r=10, t=20, b=10),
        )
        st.plotly_chart(fig2, use_container_width=True)

    desc = info.get("longBusinessSummary")
    if desc:
        st.markdown("#### 公司簡介")
        st.markdown(f"> {desc}")


# ─── Target Price Tab ─────────────────────────────────────────────────────────

def render_target_price_tab(df: pd.DataFrame, info: dict, fundamentals: dict, company_name: str):
    st.subheader(f"目標價位分析 — {company_name}")

    with st.expander("💡 加權目標價是什麼？如何計算？", expanded=False):
        st.markdown("""
**加權目標價** 是把多種獨立估值方法的結果，依各自的可信度做加權平均後得出的「合理價值中心點」，
並非預測未來股價，更近似於「目前這支股票大概值多少錢」的量化估算。

| 方法 | 權重 | 說明 |
|------|------|------|
| 分析師共識 | **×4** | 法人研究員發布的目標價，最貼近市場預期 |
| 本益比 P/E | ×2 | 以公司成長率推估合理P/E倍數，再乘以EPS |
| 股價淨值比 P/B | ×1 | 以ROE推估合理P/B倍數，再乘以每股淨值 |
| 殖利率還原 | ×1 | 股利 ÷ 合理殖利率（3.5%）反推合理價 |
| 技術阻力 | ×1 | 近6個月最高點作為短線壓力/目標參考 |

**離現價太遠的方法（>±50%）會被濾除**，避免因資料品質問題拉偏結果。
每個方法的前提假設不同，分歧愈大代表不確定性愈高。

> ⚠️ 目標價只反映公開財務數據與歷史價格，無法預知技術突破、政策變化等非線性事件。
""")

    tp = calculate_target_price(df, info, fundamentals)
    current = tp["current_price"]
    rec_target = tp.get("recommended_target")
    upside = tp.get("upside_pct")
    t_low = tp.get("target_low")
    t_high = tp.get("target_high")
    methods = tp.get("methods", [])

    if rec_target is None:
        st.warning("目標價資料不足，請確認股票代碼或稍後再試。")
        return

    # Determine which methods were filtered out (outliers)
    included_names = set()
    for m in methods:
        if 0.5 * current < m["target"] < 1.7 * current:
            included_names.add(m["name"])

    # Header metrics
    upside_color = "#4caf50" if upside >= 0 else "#f44336"
    upside_icon = "🚀" if upside >= 15 else "📈" if upside >= 5 else "➡️" if upside >= 0 else "📉"

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("現價 (TWD)", f"{current:.2f}")
    with col2:
        st.metric("加權目標價", f"{rec_target:.1f}")
    with col3:
        delta_val = rec_target - current
        st.metric("預期漲跌幅", f"{upside:+.1f}%", f"{delta_val:+.1f} TWD")
    with col4:
        st.metric("目標區間", f"{t_low:.0f} – {t_high:.0f}")

    # Big visual: current price position in target range
    st.markdown("#### 目標區間位置")
    if t_low is not None and t_high is not None and t_high > t_low:
        pos_pct = max(0, min(100, (current - t_low) / (t_high - t_low) * 100))
        tgt_pct = max(0, min(100, (rec_target - t_low) / (t_high - t_low) * 100))
        st.markdown(f"""
<div style="position:relative;margin:20px 0 30px 0;">
  <!-- Track -->
  <div style="background:#2d3548;height:16px;border-radius:8px;position:relative;">
    <!-- Target fill -->
    <div style="position:absolute;left:0;width:{tgt_pct:.0f}%;height:100%;
                background:linear-gradient(90deg,rgba(47,158,68,0.3),rgba(47,158,68,0.6));
                border-radius:8px;"></div>
    <!-- Current price marker -->
    <div style="position:absolute;left:{pos_pct:.1f}%;top:-8px;transform:translateX(-50%);
                background:#74c0fc;width:4px;height:32px;border-radius:2px;"></div>
    <!-- Target price marker -->
    <div style="position:absolute;left:{tgt_pct:.1f}%;top:-8px;transform:translateX(-50%);
                background:#4caf50;width:4px;height:32px;border-radius:2px;"></div>
  </div>
  <div style="display:flex;justify-content:space-between;margin-top:10px;font-size:13px;color:#aaa;">
    <span>低 {t_low:.1f}</span>
    <span style="color:#74c0fc;">● 現價 {current:.1f}</span>
    <span style="color:#4caf50;">★ 目標 {rec_target:.1f} ({upside:+.1f}%)</span>
    <span>高 {t_high:.1f}</span>
  </div>
</div>""", unsafe_allow_html=True)

    # Method comparison chart
    st.markdown("#### 各估值方法目標價比較")
    methods = tp.get("methods", [])
    if methods:
        method_names = [m["name"] for m in methods]
        method_targets = [m["target"] for m in methods]
        method_upsides = [m["upside_pct"] for m in methods]
        bar_colors = [
            "#4caf50" if u >= 10 else "#a9e34b" if u >= 0 else "#ff7043"
            for u in method_upsides
        ]

        fig = go.Figure()

        # Current price reference line
        fig.add_hline(
            y=current, line_dash="dash",
            line_color="#74c0fc", line_width=2,
            annotation_text=f"現價 {current:.1f}",
            annotation_position="right",
        )
        # Recommended target reference line
        fig.add_hline(
            y=rec_target, line_dash="dot",
            line_color="#4caf50", line_width=2,
            annotation_text=f"加權目標 {rec_target:.1f}",
            annotation_position="left",
        )

        fig.add_trace(go.Bar(
            x=method_names,
            y=method_targets,
            marker_color=bar_colors,
            text=[f"{t:.1f}<br>{u:+.1f}%" for t, u in zip(method_targets, method_upsides)],
            textposition="outside",
            textfont=dict(color="#fafafa", size=12),
            name="目標價",
        ))

        fig.update_layout(
            height=380,
            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
            font=dict(color="#fafafa"),
            yaxis_title="股價 (TWD)",
            xaxis_tickangle=-20,
            showlegend=False,
            margin=dict(l=10, r=120, t=40, b=60),
        )
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.06)")
        st.plotly_chart(fig, use_container_width=True)

    # Detailed method breakdown
    st.markdown("#### 估值方法詳情")
    st.caption("✅ 納入加權 = 目標價落在現價 ±50% 內。❌ 排除 = 數值偏離過遠，視為資料品質問題，不計入加權平均。")

    yf_symbol = info.get("symbol", "")  # e.g. "2884.TW"

    for m in methods:
        upside_m = m["upside_pct"]
        conf_color = m["confidence_color"]
        up_color = "#4caf50" if upside_m >= 0 else "#f44336"
        up_icon = "▲" if upside_m >= 0 else "▼"
        is_included = m["name"] in included_names
        included_badge = (
            '<span style="font-size:11px;color:#4caf50;margin-left:8px;">✅ 納入加權</span>'
            if is_included else
            '<span style="font-size:11px;color:#888;margin-left:8px;">❌ 偏離過遠，排除</span>'
        )
        opacity = "1.0" if is_included else "0.45"

        # Source link — only for analyst consensus (yfinance data from Yahoo Finance)
        source_html = ""
        if m["name"] == "分析師共識目標價" and yf_symbol:
            yf_url = f"https://finance.yahoo.com/quote/{yf_symbol}/analysis/"
            source_html = (
                f"<div style='margin-top:8px;'>"
                f"<a href='{yf_url}' target='_blank' "
                f"style='display:inline-block;padding:3px 10px;"
                f"background:#1565c0;color:#e3f2fd;border-radius:5px;"
                f"font-size:12px;font-weight:600;text-decoration:none;'>🔗 Yahoo Finance 分析師頁面</a>"
                f"<span style='font-size:11px;color:#78909c;margin-left:8px;'>"
                f"資料來源：yfinance / Yahoo Finance 分析師預測匯總</span>"
                f"</div>"
            )
        elif m["name"] not in ("分析師共識目標價",):
            source_html = (
                "<div style='margin-top:6px;font-size:11px;color:#546e7a;'>📐 依公開財報數據模型估算</div>"
            )

        st.markdown(f"""
<div style="background:#1e2130;border-radius:8px;padding:14px 18px;margin:8px 0;
            border-left:4px solid {conf_color};opacity:{opacity};">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <div>
      <span style="font-size:20px">{m['emoji']}</span>
      <span style="font-weight:700;margin-left:8px;">{m['name']}</span>
      <span style="font-size:12px;color:{conf_color};margin-left:10px;
                   border:1px solid {conf_color};border-radius:4px;padding:2px 6px;">
        信心度: {m['confidence']}
      </span>
      {included_badge}
    </div>
    <div style="text-align:right;">
      <div style="font-size:22px;font-weight:900;">TWD {m['target']:.1f}</div>
      <div style="color:{up_color};font-size:14px;">{up_icon} {upside_m:+.1f}%</div>
    </div>
  </div>
  <div style="font-size:12px;color:#aaa;margin-top:8px;">{m['detail']}</div>
  {source_html}
</div>""", unsafe_allow_html=True)

    # ── Analyst targets from news ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 🏦 法人目標價（新聞提取）")
    st.caption(
        "從 Google News 標題 + 部分內文自動提取外資機構目標價。"
        "幣別已自動辨識：TWD（台幣）= 可直接比較；USD = ADR 美元報價，不可直接比較現股。"
        "僅供輔助參考，請點連結確認原文。"
    )

    with st.spinner("搜尋法人目標價新聞..."):
        analyst_targets = get_analyst_targets(
            info.get("symbol", ""),
            company_name,
            current,
        )

    if analyst_targets:
        for at in analyst_targets:
            cur = at.get("currency", "unknown")
            dir_color = "#4caf50" if at["direction"] == "↑" else "#f44336" if at["direction"] == "↓" else "#aaa"

            # Currency badge
            if cur == "TWD":
                cur_badge = "<span style='font-size:11px;background:#1b5e20;color:#a5d6a7;border-radius:3px;padding:1px 5px;margin-left:6px;'>TWD</span>"
            elif cur == "USD":
                cur_badge = "<span style='font-size:11px;background:#b71c1c;color:#ffcdd2;border-radius:3px;padding:1px 5px;margin-left:6px;'>USD ⚠️ ADR</span>"
            else:
                cur_badge = "<span style='font-size:11px;background:#37474f;color:#cfd8dc;border-radius:3px;padding:1px 5px;margin-left:6px;'>幣別不明</span>"

            # Upside badge
            if at["upside_pct"] is not None:
                uc = "#4caf50" if at["upside_pct"] >= 0 else "#f44336"
                upside_html = f"<span style='color:{uc};font-weight:700;font-size:15px;'>{at['upside_pct']:+.1f}%</span>"
            else:
                upside_html = "<span style='color:#888;'>N/A（USD 不可比）</span>"

            # Rating
            rating_html = ""
            if at.get("rating"):
                rating_html = f"<span style='color:{at['rating_color']};font-weight:600;margin-left:8px;'>{at['rating']}</span>"

            link = at.get("link") or "#"
            title = at.get("title", "")
            pub = at.get("pub_date", "")[:16]
            source_name = at.get("source", "") or ""
            adr_note = at.get("adr_note", "")
            excerpt = at.get("excerpt", "")

            has_link = link and link != "#"
            link_btn = ""
            if has_link:
                link_btn = (
                    f"<a href='{link}' target='_blank' "
                    f"style='display:inline-block;margin-top:8px;padding:4px 10px;"
                    f"background:#1565c0;color:#e3f2fd;border-radius:5px;"
                    f"font-size:12px;font-weight:600;text-decoration:none;'>🔗 查看原文</a>"
                )
                if source_name:
                    link_btn += (
                        f"<span style='font-size:11px;color:#78909c;margin-left:8px;'>"
                        f"來源：{source_name}</span>"
                    )

            excerpt_html = ""
            if excerpt:
                excerpt_html = (
                    f"<div style='font-size:11px;color:#90a4ae;margin-top:6px;"
                    f"padding:5px 8px;background:#131c2b;border-radius:4px;"
                    f"border-left:2px solid #1565c0;'>"
                    f"📄 {excerpt}</div>"
                )
            adr_html = ""
            if adr_note:
                adr_html = (
                    f"<div style='font-size:11px;color:#ffcc80;margin-top:4px;'>"
                    f"ℹ️ {adr_note}</div>"
                )

            st.markdown(f"""
<div style="background:#1a2035;border:1px solid #2d3548;border-radius:8px;
            padding:12px 16px;margin:6px 0;">
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;">
    <span style="font-size:16px;">{at['flag']}</span>
    <span style="font-weight:700;font-size:15px;">{at['bank']}</span>
    {cur_badge}
    <span style="font-size:18px;font-weight:900;margin-left:8px;">{at['price_note']}</span>
    <span style="color:{dir_color};font-size:16px;">{at['direction']}</span>
    {upside_html}
    {rating_html}
    <span style="color:#666;font-size:11px;margin-left:auto;">{pub}</span>
  </div>
  <div style="font-size:13px;color:#b0bec5;line-height:1.4;">
    {title[:120]}{'…' if len(title)>120 else ''}
  </div>
  {link_btn}
  {adr_html}
  {excerpt_html}
</div>""", unsafe_allow_html=True)
    else:
        st.info("未在近期新聞標題中找到可解析的法人目標價。如需確認，請直接查詢 Bloomberg 或各券商研究報告。")

    st.markdown("---")
    st.caption(
        "⚠️ 量化目標價為模型估算，法人目標價為新聞標題自動提取（非官方資料），"
        "均不能預知技術突破、法規變化、市場黑天鵝等非線性事件的影響，僅供參考。"
    )


# ─── Backtest verification ────────────────────────────────────────────────────

# Map each horizon to the forward window (trading days) used to judge its call.
_HORIZON_WINDOWS = [
    ("ultra_short", "極短線", 3),
    ("short", "短線", 5),
    ("medium", "中線", 20),
    ("long", "長線", 60),
]


def _verdict(score: int, fwd: float):
    """Judge a horizon's call (by score→direction) against the realized forward
    return. Returns (icon, label, color)."""
    if fwd is None:
        return "⏳", "尚無足夠後續資料", "#78909c"
    if score >= 58:          # bullish call
        if fwd > 1:
            return "✅", "準確（建議偏多，之後上漲）", "#4caf50"
        if fwd < -1:
            return "❌", "失準（建議偏多，之後下跌）", "#f44336"
        return "➖", "大致持平", "#ff9800"
    if score < 48:           # bearish call
        if fwd < -1:
            return "✅", "準確（建議偏空，之後下跌）", "#4caf50"
        if fwd > 1:
            return "❌", "失準（建議偏空，之後上漲）", "#f44336"
        return "➖", "大致持平", "#ff9800"
    # neutral / hold
    if abs(fwd) <= 3:
        return "✅", "準確（建議觀望，之後盤整）", "#4caf50"
    return "➖", "觀望但後續有明顯波動", "#ff9800"


def render_backtest_verification(df, df_full, timeframe_recs, rec):
    st.markdown("---")
    st.markdown("#### 📅 事後驗證：當時的建議準不準？")

    as_of_last = df.index[-1]
    as_of_close = float(df["Close"].iloc[-1])
    full_dates = list(df_full.index)
    try:
        pos = next(i for i, d in enumerate(full_dates) if d.date() == as_of_last.date())
    except StopIteration:
        st.info("找不到基準日對應的後續資料，無法驗證。")
        return

    closes = df_full["Close"]
    n_future = len(full_dates) - 1 - pos
    latest_close = float(closes.iloc[-1])
    latest_date = full_dates[-1].date()
    total_fwd = (latest_close / as_of_close - 1) * 100

    st.caption(
        f"基準日收盤 **{as_of_close:.2f}**（{as_of_last.date()}）→ 最新收盤 "
        f"**{latest_close:.2f}**（{latest_date}），期間共 {n_future} 個交易日、"
        f"實際 {total_fwd:+.1f}%。以下比對各週期「當時的建議」與「事後實際走勢」。"
    )

    rec_by_key = {h["key"]: h for h in timeframe_recs}
    correct = 0
    judged = 0
    rows_html = []
    for key, name, win in _HORIZON_WINDOWS:
        h = rec_by_key.get(key)
        if not h:
            continue
        fpos = pos + win
        if fpos < len(full_dates):
            fwd_close = float(closes.iloc[fpos])
            fwd = (fwd_close / as_of_close - 1) * 100
            fwd_date = full_dates[fpos].date()
        else:
            fwd = None
            fwd_date = None
        icon, label, vcolor = _verdict(h["score"], fwd)
        if fwd is not None:
            judged += 1
            if icon == "✅":
                correct += 1
        fwd_str = f"{fwd:+.1f}%" if fwd is not None else "—"
        fwd_date_str = f"（到 {fwd_date}）" if fwd_date else "（尚未到）"
        rows_html.append(f"""
<tr style="border-bottom:1px solid #2d3548;">
  <td style="padding:8px 10px;font-weight:700;">{h['name']}<br>
      <span style="font-size:11px;color:#90a4ae;">{h['span']}</span></td>
  <td style="padding:8px 10px;color:{h['color']};font-weight:700;">
      {h['icon']} {h['action']}<br>
      <span style="font-size:11px;color:#888;">評分 {h['score']}</span></td>
  <td style="padding:8px 10px;">+{win} 交易日<br>
      <span style="font-size:11px;color:#90a4ae;">{fwd_date_str}</span></td>
  <td style="padding:8px 10px;font-weight:800;color:{'#4caf50' if (fwd or 0)>=0 else '#f44336'};">
      {fwd_str}</td>
  <td style="padding:8px 10px;color:{vcolor};font-weight:600;">{icon} {label}</td>
</tr>""")

    acc_str = f"{correct}/{judged} 命中" if judged else "後續資料不足"
    acc_pct = (correct / judged * 100) if judged else 0
    acc_color = "#4caf50" if acc_pct >= 60 else "#ff9800" if acc_pct >= 40 else "#f44336"
    st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:13px;background:#161b26;border-radius:8px;">
  <thead>
    <tr style="background:#1e2130;color:#b0bec5;font-size:12px;">
      <th style="padding:8px 10px;text-align:left;">週期</th>
      <th style="padding:8px 10px;text-align:left;">當時建議</th>
      <th style="padding:8px 10px;text-align:left;">驗證窗口</th>
      <th style="padding:8px 10px;text-align:left;">實際漲跌</th>
      <th style="padding:8px 10px;text-align:left;">結果</th>
    </tr>
  </thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
</div>
<div style="margin-top:10px;font-size:14px;">
  本次回測命中率：<span style="color:{acc_color};font-weight:800;">{acc_str}</span>
</div>""", unsafe_allow_html=True)
    st.caption(
        "⚠️ 命中判定：偏多建議之後上漲、偏空建議之後下跌、觀望之後盤整即視為命中。"
        "短週期（極短／短線）幾乎全由當日技術面與價量籌碼還原，回測較貼近真實；"
        "中長線含基本面／消息面成分，因免費資料無法完全還原到當日，僅供參考。"
    )


# ─── Recommendation Tab ───────────────────────────────────────────────────────

def render_recommendation_tab(
    df: pd.DataFrame, stock_id: str, info: dict,
    company_name: str, financials: dict,
    as_of_date=None, df_full=None,
):
    is_backtest = as_of_date is not None
    as_of_str = as_of_date.strftime("%Y%m%d") if is_backtest else ""
    title_suffix = f"（回測 {as_of_date}）" if is_backtest else ""
    st.subheader(f"投資建議 — {company_name} ({stock_id}){title_suffix}")

    with st.spinner("計算投資評分..."):
        # Shared core — identical to what 智能選股 runs (services/analysis.py)。
        # 個股頁一律抓新聞：只看一檔時多花幾秒無妨，而消息面正是這裡的重點之一。
        a = compute_scores(df, info, financials, stock_id, company_name,
                           as_of_date=as_of_date)
        tech_score = a["tech_score"]; fund_score = a["fund_score"]; news_score = a["news_score"]
        fundamentals = a["fundamentals"]
        tp = a["tp"]; target_upside = a["target_upside"]
        margin_signal = a["margin_signal"]; margin_trend = a["margin_trend"]
        volume_signal = a["volume_signal"]; market_regime = a["market_regime"]
        rec = a["rec"]; rationale = a["rationale"]; risk_plan = a["risk_plan"]
        timeframe_recs = a["horizon_cards"]; potential = a["potential"]

    # 主視覺改用「長線結構分」——實證 t=4.71，是綜合評分(t=2.04)的兩倍強度。
    # 原本最大的儀表板顯示最弱的訊號，等於把使用者的注意力導向最不可靠的數字。
    # 用**純技術**長線分（回測驗證的就是它）；混合分另外顯示
    _long_sc = a["horizon_tech"]["long"]["score"]

    _long_act = _action_for_score(_long_sc)
    _pf_thr = long_threshold()

    col_gauge, col_details = st.columns([1, 2])

    with col_gauge:
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=_long_sc,
            title={"text": "長線結構分（純技術）✅實證最強",
                   "font": {"size": 15, "color": "#fafafa"}},
            number={"font": {"size": 52, "color": _long_act["color"]}},
            gauge={
                "axis": {"range": [0, 100], "tickfont": {"color": "#fafafa"}},
                "bar": {"color": _long_act["color"], "thickness": 0.3},
                "steps": [
                    {"range": [0, _pf_thr], "color": "rgba(240,62,62,0.18)"},
                    {"range": [_pf_thr, 100], "color": "rgba(76,175,80,0.22)"},
                ],
                "threshold": {
                    "line": {"color": "#ffd54f", "width": 4},
                    "thickness": 0.9, "value": _pf_thr,
                },
                "bgcolor": "rgba(0,0,0,0)",
            },
        ))
        fig_gauge.update_layout(
            height=300, paper_bgcolor="#0e1117",
            font=dict(color="#fafafa"),
            margin=dict(l=30, r=30, t=50, b=10),
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

        _pass = _long_sc >= _pf_thr
        st.markdown(f"""
<div style="background:{'#0a3d1c' if _pass else '#3d2a0a'};
            border:2px solid {_long_act['color']};
            border-radius:12px;padding:16px;text-align:center;">
  <div style="font-size:30px">{_long_act['icon']}</div>
  <div style="font-size:24px;font-weight:900;color:{_long_act['color']}">
    {_long_act['action']}</div>
  <div style="font-size:12px;color:#cfd8dc;margin-top:6px;">
    {'✅ 已達實證門檻' if _pass else '⚠️ 未達實證門檻'} {_pf_thr:.0f} 分
  </div>
</div>""", unsafe_allow_html=True)
        st.caption(
            f"依 179 期回測，長線結構分 **≥{_pf_thr:.0f} 分**的區間 1個月超額報酬才轉正；"
            "此分數的預測力（t=4.71）約為下方綜合評分（t=2.04）的兩倍。\n\n"
            f"下方「四種週期評分」用的是同一個純技術分數，前後一致。"
        )

        # 綜合評分退居輔助
        st.markdown(f"""
<div style="background:#1a2035;border:1px solid {rec['color']};border-radius:10px;
            padding:10px 14px;text-align:center;margin-top:10px;">
  <div style="font-size:11px;color:#90a4ae;">綜合評分（技術+基本面+消息+目標價）</div>
  <div style="font-size:26px;font-weight:900;color:{rec['color']}">{rec['total_score']}</div>
  <div style="font-size:13px;color:{rec['color']}">{rec['icon']} {rec['action']}</div>
</div>""", unsafe_allow_html=True)

        # 兩個分數常常不一致，直接說明為什麼，而不是讓使用者自己猜
        if abs(_long_sc - rec["total_score"]) >= 15:
            higher = "長線結構分" if _long_sc > rec["total_score"] else "綜合評分"
            st.info(
                f"❓ **為什麼兩個分數差這麼多？**（{_long_sc} vs {rec['total_score']}）\n\n"
                "它們量的是**不同東西**，不是互相矛盾：\n\n"
                f"· **長線結構分 {_long_sc}**＝只看**價格結構**"
                "（季線位置、均線排列、半年報酬）。這是回測驗證過的**進場時機**訊號。\n\n"
                f"· **綜合評分 {rec['total_score']}**＝再加上基本面（{rec['fund_score']}）、"
                f"消息（{rec['news_score']}）、目標價，並扣除融資籌碼風險。"
                "這是**體質與風險**的總覽，但預測力較弱（t=2.04）。\n\n"
                f"目前 **{higher}** 較高。常見情境：**股價長期走勢很強、但基本面偏弱或"
                "散戶槓桿過重**——趨勢還在，但底子與籌碼有隱憂。"
                "兩者都看，不要只憑一個數字下決定。"
            )

    with col_details:
        st.markdown(f"#### 評分分解")
        st.caption(rec.get("weight_note", ""))

        def score_bar(label, score, note=""):
            c = "#f03e3e" if score < 40 else "#4caf50" if score > 60 else "#ff9800"
            note_html = f'<span style="font-size:11px;color:#aaa;margin-left:8px;">{note}</span>' if note else ""
            st.markdown(f"""
<div style="margin:8px 0;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
    <span style="font-weight:600">{label}{note_html}</span>
    <span style="color:{c};font-weight:700">{score}/100</span>
  </div>
  <div style="background:#1e2130;border-radius:4px;height:12px;overflow:hidden;">
    <div style="background:{c};width:{score}%;height:100%;border-radius:4px;"></div>
  </div>
</div>""", unsafe_allow_html=True)

        has_tp = rec.get("tp_score") is not None
        if has_tp:
            score_bar("📊 技術面", rec["tech_score"], "×35%")
            score_bar("💹 基本面", rec["fund_score"], "×35%")
            score_bar("📰 消息面", rec["news_score"], "×15%")
            upside = rec.get("target_upside_pct", 0) or 0
            tp_note = f"目標價 {upside:+.1f}%  ×15%"
            score_bar("💰 目標價面", rec["tp_score"], tp_note)
        else:
            score_bar("📊 技術面", rec["tech_score"], "×40%")
            score_bar("💹 基本面", rec["fund_score"], "×40%")
            score_bar("📰 消息面", rec["news_score"], "×20%")

        st.markdown(f"""
<div style="background:#1e2130;border-radius:8px;padding:14px;margin-top:16px;">
  <div style="font-style:italic;color:#ccc;">{rec['summary']}</div>
</div>""", unsafe_allow_html=True)

    # ── 為什麼這麼建議（理由綜合說明）─────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 🧭 為什麼給這個建議")

    # Market regime context — the bar this score had to clear
    if market_regime and market_regime.get("regime") != "unknown":
        mr_color = market_regime["color"]
        adj = rec.get("regime_adj", 0)
        adj_txt = (f"買進門檻 {58:+d} → <b>{rec.get('buy_threshold', 58)}</b>"
                   if adj else "買進門檻維持 58")
        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {mr_color};border-radius:6px;
            padding:8px 14px;margin:0 0 10px 0;">
  <span style="color:{mr_color};font-weight:700;">大盤環境：{market_regime['label']}</span>
  <span style="color:#cfd8dc;font-size:13px;"> — {market_regime['ma_note']}（{adj_txt}）</span>
</div>""", unsafe_allow_html=True)

    supports_html = "".join(
        f"<li style='margin:3px 0;color:#a5d6a7;'>{s}</li>" for s in rationale["supports"]
    ) or "<li style='color:#888;'>目前無明顯偏多因素</li>"
    pressures_html = "".join(
        f"<li style='margin:3px 0;color:#ef9a9a;'>{p}</li>" for p in rationale["pressures"]
    ) or "<li style='color:#888;'>目前無明顯偏空／風險因素</li>"
    st.markdown(f"""
<div style="background:#161b26;border:1px solid {rec['color']};border-radius:10px;
            padding:16px 18px;">
  <div style="font-size:15px;font-weight:700;color:{rec['color']};margin-bottom:6px;">
    {rec['icon']} {rationale['headline']}
  </div>
  <div style="font-size:13px;color:#cfd8dc;line-height:1.6;margin-bottom:12px;">
    {rationale['synthesis']}
  </div>
  <div style="display:flex;gap:24px;flex-wrap:wrap;">
    <div style="flex:1;min-width:240px;">
      <div style="font-size:13px;font-weight:700;color:#4caf50;margin-bottom:2px;">✅ 支撐買進的理由</div>
      <ul style="margin:0;padding-left:18px;font-size:12px;">{supports_html}</ul>
    </div>
    <div style="flex:1;min-width:240px;">
      <div style="font-size:13px;font-weight:700;color:#f44336;margin-bottom:2px;">⚠️ 偏空／需留意的理由</div>
      <ul style="margin:0;padding-left:18px;font-size:12px;">{pressures_html}</ul>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown("---")
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        st.markdown("#### 📊 技術面根據")
        for reason in rec["tech_reasons"]:
            icon = "🟢" if "+" in reason else "🔴" if "-" in reason else "⚪"
            st.markdown(f"{icon} {reason}")
    with r2:
        st.markdown("#### 💹 基本面根據")
        for reason in rec["fund_reasons"]:
            icon = "🟢" if "+" in reason else "🔴" if "-" in reason else "⚪"
            st.markdown(f"{icon} {reason}")
    with r3:
        st.markdown("#### 📰 消息面")
        for reason in rec["news_reasons"]:
            st.markdown(f"　{reason}")
    with r4:
        st.markdown("#### 💰 目標價面")
        tp_r = rec.get("tp_reason")
        if tp_r:
            icon = "🟢" if "+" in tp_r else "🔴" if "-" in tp_r else "⚪"
            st.markdown(f"{icon} {tp_r}")
        else:
            st.markdown("⚪ 目標價資料不足")

    # ── Timeframe-specific recommendations ────────────────────────────────────
    st.markdown("---")
    st.markdown("#### ⏱ 四種週期評分")
    st.info(
        "📌 **這四個分數的差別是「用多長週期的指標計算」，不是「建議你抱多久」。**\n\n"
        "兩者互相獨立——實證顯示**即使你只想抱一週，用「長線分」選股仍然最好**"
        "（持有1週：長線分超額 +0.53%／t=2.61，優於短線分 +0.39%）。"
        "所以不要因為想做短線就去看短線分。"
    )
    tf_cols = st.columns(4)
    for tcol, h in zip(tf_cols, timeframe_recs):
        with tcol:
            eff = ev_horizon_efficacy(h["key"])
            drivers_html = "".join(
                f"<div style='font-size:11px;color:#b0bec5;margin-top:3px;'>• {d}</div>"
                for d in h["drivers"]
            )
            eff_badge = (
                f"<span style='font-size:10px;background:{eff['color']}22;"
                f"color:{eff['color']};border:1px solid {eff['color']};"
                f"border-radius:4px;padding:1px 5px;margin-left:6px;'>"
                f"{eff['tag']} {eff['label']}</span>" if eff else ""
            )
            st.markdown(f"""
<div style="background:{rec['bg_color'] if False else '#1a2035'};
            border:1px solid {h['color']};border-radius:10px;padding:12px 14px;
            margin:4px 0;min-height:200px;">
  <div style="font-size:15px;font-weight:800;">{h['name']}{eff_badge}</div>
  <div style="font-size:11px;color:#90a4ae;margin-bottom:8px;">{h['span']}</div>
  <div style="display:flex;align-items:baseline;gap:8px;">
    <span style="font-size:30px;font-weight:900;color:{h['color']};">{h['score']}</span>
    <span style="font-size:13px;color:#888;">/100</span>
  </div>
  <div style="font-size:15px;font-weight:700;color:{h['color']};margin:4px 0 8px 0;">
    {h['icon']} {h['action']}
  </div>
  <div style="font-size:11px;color:#78909c;border-top:1px solid #2d3548;
              padding-top:6px;">{h['desc']}</div>
  {drivers_html}
</div>""", unsafe_allow_html=True)
            if eff:
                st.caption(f"{eff['tag']} {eff['note']}")

    # ── Potential (潛力) — borrowed from 智能選股 so the detail page explains
    #    why this stock does (or doesn't) qualify as a 潛伏股 ──────────────────
    st.markdown("---")
    qual = potential.get("qualifies")
    st.markdown(f"#### 🌱 潛力面：還有沒有上漲空間？　"
                f"{'✅ 符合潛伏股條件' if qual else '❌ 未達潛伏股條件'}")
    st.caption(
        "與上方「綜合評分」互補：綜合評分看『現在強不強』，潛力分看『還有沒有空間』。"
        "同一檔可能綜合分高但潛力低（已大漲），或反之。"
    )
    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
    with pc1:
        st.metric("潛力總分", potential.get("total", 0))
    with pc2:
        st.metric("📰 話題", potential.get("buzz", 0), f"{potential.get('pos_catalysts',0)} 催化劑")
    with pc3:
        st.metric("🚀 前瞻", potential.get("prospect", 0))
    with pc4:
        st.metric("💰 空間", potential.get("upside_score", 0))
    with pc5:
        st.metric("🌱 低基期", potential.get("low_base", 0),
                  f"52週 {potential.get('position_pct', 0):.0f}%")
    st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {'#7986cb' if qual else '#546e7a'};
            border-radius:6px;padding:10px 14px;margin:6px 0;">
  <span style="color:#cfd8dc;font-size:13px;">{potential.get('summary','')}</span>
</div>""", unsafe_allow_html=True)
    with st.expander("潛力面各構面理由", expanded=False):
        pe1, pe2 = st.columns(2)
        with pe1:
            st.markdown("**📰 話題**")
            for x in potential.get("buzz_reasons", []) or ["—"]:
                st.markdown(f"　• {x}")
            st.markdown("**🚀 前瞻（成長）**")
            for x in potential.get("prospect_reasons", []) or ["成長數據有限"]:
                st.markdown(f"　• {x}")
        with pe2:
            st.markdown("**💰 空間（目標價）**")
            for x in potential.get("upside_reasons", []) or ["目標價資料有限"]:
                st.markdown(f"　• {x}")
            st.markdown("**🌱 低基期（是否還沒漲）**")
            for x in potential.get("low_base_reasons", []) or ["—"]:
                st.markdown(f"　• {x}")

    # ── Risk plan: turn the call into something executable ────────────────────
    if risk_plan:
        st.markdown("---")
        st.markdown("#### 🛡 風險控管計畫（停損／停利／風報比）")
        st.caption(
            "光說「買進」不夠——這裡用 ATR（真實波動幅度）換算出場點。停損取「現價−2×ATR」"
            "與近20日低點中較合理者；停利優先用加權目標價。"
        )
        st.warning(
            "⚠️ **回測實證：機械式 2×ATR 停損會嚴重侵蝕長期報酬**——139 期回測顯示，"
            "同樣的選股加上此停損後，超額報酬 1個月由 +3.10% 降至 +1.80%、"
            "**3個月由 +12.54% 大幅降至 +4.21%**（持有越久傷害越大，因為會被洗出後續反彈的部位）。"
            "另外「依風報比 R:R 排序選股」在回測中**無效**（超額報酬為負）。"
            "→ 建議把停損價當作**風險意識與部位控管的參考**，而非機械執行的出場規則。"
        )
        rp = risk_plan
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.metric("建議停損", f"{rp['stop']:.2f}", f"{rp['stop_pct']:.1f}%")
        with k2:
            st.metric("停利目標", f"{rp['take']:.2f}", f"{rp['take_pct']:+.1f}%")
        with k3:
            st.metric("風險報酬比 R:R",
                      f"{rp['rr']:.2f}" if rp["rr"] else "N/A", rp["verdict"])
        with k4:
            st.metric("波動度 (ATR)", f"{rp['atr_pct']:.1f}%", rp["vol_label"])
        pos = rp.get("suggested_position_pct")
        pos_txt = (f"若單筆最多虧損總資金 2%，此標的建議部位上限約 "
                   f"**{pos:.0f}%**（停損幅度 {abs(rp['stop_pct']):.1f}%）。") if pos else ""
        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {rp['verdict_color']};
            border-radius:6px;padding:10px 14px;margin:6px 0;">
  <span style="color:{rp['verdict_color']};font-weight:700;">{rp['verdict']}</span>
  <span style="color:#cfd8dc;font-size:13px;">
    — 每股風險 {abs(rp['stop_pct']):.1f}%、潛在報酬 {rp['take_pct']:+.1f}%
    （停利依據：{rp['take_source']}）</span>
</div>""", unsafe_allow_html=True)
        if pos_txt:
            st.caption(pos_txt)

    # ── 這檔的長線分落在哪個實證區間？ ────────────────────────────────────────
    long_sc = a["horizon_tech"]["long"]["score"]
    if long_sc is not None:
        b = ev_bucket("長線+量能確認", long_sc, 20) or ev_bucket("長線結構分", long_sc, 20)
        thr = long_threshold()
        if b:
            ok = b.get("excess", 0) > 0
            bc = "#4caf50" if ok else "#f44336"
            st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {bc};border-radius:6px;
            padding:10px 14px;margin:8px 0;">
  <span style="color:{bc};font-weight:700;">
    {'✅' if ok else '⚠️'} 長線結構分 {long_sc} 落在 {b['range']} 區間</span>
  <span style="color:#cfd8dc;font-size:13px;">
    — 歷史上此區間持有1個月的**勝率 {b.get('win_rate', 0):.1f}%、超額報酬
    {b.get('excess', 0):+.2f}%**（樣本 {b.get('n', 0):,} 筆）
    {'，屬於值得進場的區間。' if ok else f'。實證門檻約 {thr:.0f} 分，此分數以下歷史超額為負，追價需謹慎。' if thr else '。'}
  </span>
</div>""", unsafe_allow_html=True)

    # ── Backtest verification: did the as-of recommendation pan out? ───────────
    if is_backtest and df_full is not None:
        render_backtest_verification(df, df_full, timeframe_recs, rec)

    # ── Volume (量價關係) section ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📊 量價關係（成交量．多時間尺度）")
    st.caption(
        "成交量是判斷「漲跌是否有量能支撐」的關鍵，也與融資籌碼互相牽動。除了今日量能，"
        "更比較 5日／20日／60日均量：5日>20日>60日＝量能持續放大（趨勢扎實）；"
        "反之為量能萎縮；僅短期爆量而長期基期低則追價須存疑。"
    )
    if volume_signal.get("vol_ratio") is not None:
        vs_rel = volume_signal["relationship"]
        vs_sig = volume_signal["signal"]
        vs_color = {"bullish": "#4caf50", "bearish": "#f44336"}.get(vs_sig, "#ff9800")
        vr = volume_signal["vol_ratio"]
        vp5 = volume_signal.get("price5_pct")
        vp5_str = f"{vp5:+.1f}%" if vp5 is not None else "N/A"
        struct = volume_signal.get("vol_structure", "")
        ma5 = volume_signal.get("vol_ma5")
        ma20 = volume_signal.get("vol_ma20")
        ma60 = volume_signal.get("vol_ma60")
        sm = volume_signal.get("short_mid_ratio")
        ml = volume_signal.get("mid_long_ratio")

        def _lots(v):  # shares → 張 (1 張 = 1000 股)
            return f"{v/1000:,.0f} 張" if v else "N/A"

        vcol1, vcol2, vcol3, vcol4 = st.columns(4)
        with vcol1:
            st.metric("量價型態", vs_rel)
        with vcol2:
            st.metric("今日量能", f"{vr:.1f}x 月均量", "爆量" if volume_signal.get("is_spike") else None)
        with vcol3:
            st.metric("量能結構", struct)
        with vcol4:
            st.metric("近5日股價", vp5_str)

        # Multi-timescale volume averages
        mcol1, mcol2, mcol3 = st.columns(3)
        with mcol1:
            st.metric("短期均量 (5日)", _lots(ma5),
                      f"為20日 {sm*100:.0f}%" if sm else None)
        with mcol2:
            st.metric("中期均量 (20日)", _lots(ma20),
                      f"為60日 {ml*100:.0f}%" if ml else None)
        with mcol3:
            st.metric("長期均量 (60日)", _lots(ma60))

        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {vs_color};border-radius:6px;
            padding:10px 14px;margin:6px 0;">
  <span style="color:{vs_color};font-weight:700;">「{vs_rel}」</span>
  <span style="color:#cfd8dc;font-size:13px;">— {volume_signal['explain']}</span>
</div>""", unsafe_allow_html=True)
    else:
        st.info("成交量資料不足（需至少約 25 個交易日），無法判讀量價關係。")

    # ── Margin (融資) chip-structure section ──────────────────────────────────
    st.markdown("---")
    st.markdown("#### 💳 融資（散戶槓桿）籌碼面：數量水準 × 增減趨勢")
    st.caption(
        "融資＝散戶借錢買股。看兩件事：①「使用率」= 融資餘額 ÷ 融資限額（數量相對股本，"
        "過高＝籌碼凌亂）；②「餘額增減趨勢」= 融資餘額近期是加碼還是退場（融資大增＝散戶追"
        "槓桿、籌碼變重；融資大減＝去槓桿、籌碼安定）。資料來源：證交所每日信用交易統計。"
    )
    if margin_signal.get("level") == "na":
        st.info(margin_signal["reasons"][0])
    else:
        usage = margin_signal["usage_pct"]
        level = margin_signal["level"]
        lvl_map = {
            "high": ("#f44336", "偏高 ⚠️"),
            "elevated": ("#ff9800", "略高"),
            "normal": ("#4caf50", "正常"),
            "low": ("#4caf50", "正常"),
        }
        lvl_color, lvl_label = lvl_map.get(level, ("#78909c", level))
        bar_pct = max(0, min(100, usage))
        chg5 = margin_signal.get("change_5d")
        chg_long = margin_signal.get("change_long")
        long_days = margin_signal.get("long_days", 0)
        trend_label = margin_signal.get("trend_label", "資料不足")
        chg5_str = f"{chg5:+.1f}%" if chg5 is not None else "N/A"
        chg_long_str = f"{chg_long:+.1f}%" if chg_long is not None else "N/A"
        trend_color = {
            "大增": "#f44336", "增加": "#ff9800", "持平": "#78909c",
            "減少": "#4caf50", "大減": "#4caf50",
        }.get(trend_label, "#78909c")
        bal = margin_signal.get("balance")
        bal_str = f"{bal:,.0f} 張" if bal is not None else "N/A"
        short_bal = margin_signal.get("short_balance")
        m_date = margin_signal.get("date", "")

        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        with mc1:
            st.metric("融資使用率", f"{usage:.1f}%", lvl_label)
        with mc2:
            st.metric("融資餘額（數量）", bal_str)
        with mc3:
            st.metric("融資趨勢", trend_label,
                      f"{long_days}日 {chg_long_str}" if chg_long is not None else None)
        with mc4:
            st.metric("近5日融資變化", chg5_str)
        with mc5:
            st.metric("融券餘額", f"{short_bal:,.0f} 張" if short_bal is not None else "N/A")

        # Trend banner — makes the 增減趨勢 verdict explicit
        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {trend_color};border-radius:6px;
            padding:8px 14px;margin:4px 0 8px 0;">
  <span style="color:{trend_color};font-weight:700;">融資餘額趨勢：{trend_label}</span>
  <span style="color:#cfd8dc;font-size:13px;">
    （近 {long_days} 日 {chg_long_str}、近 5 日 {chg5_str}）
    — {'散戶加碼槓桿、籌碼變重，偏空注意' if trend_label in ('大增','增加') else '散戶去槓桿、籌碼趨安定，偏多' if trend_label in ('大減','減少') else '融資水位穩定'}
  </span>
</div>""", unsafe_allow_html=True)

        st.markdown(f"""
<div style="margin:6px 0 4px 0;">
  <div style="background:#2d3548;height:14px;border-radius:7px;position:relative;overflow:hidden;">
    <div style="width:{bar_pct:.0f}%;height:100%;background:{lvl_color};border-radius:7px;"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:11px;color:#78909c;margin-top:3px;">
    <span>0%</span>
    <span style="color:#ff9800;">警戒 ~15%</span>
    <span style="color:#f44336;">偏高 ~27%</span>
    <span>50%</span>
  </div>
</div>""", unsafe_allow_html=True)

        # ── 量價 × 融資趨勢圖（股價+融資餘額 上 / 成交量 下，同一交易日對齊）──────
        if len(margin_trend) >= 2:
            # Map df closes & volumes by date for alignment with margin dates (YYYYMMDD)
            close_by_date, vol_by_date = {}, {}
            for idx, row in df[["Close", "Volume"]].iterrows():
                try:
                    key = idx.strftime("%Y%m%d")
                    close_by_date[key] = float(row["Close"])
                    vol_by_date[key] = float(row["Volume"])
                except Exception:
                    pass
            x_labels = [f"{p['date'][4:6]}/{p['date'][6:8]}" for p in margin_trend]
            balances = [p["balance"] for p in margin_trend]
            prices = [close_by_date.get(p["date"]) for p in margin_trend]
            volumes = [vol_by_date.get(p["date"]) for p in margin_trend]
            # colour volume bars by that day's price direction
            vol_colors = []
            for i, p in enumerate(prices):
                if i == 0 or p is None or prices[i - 1] is None:
                    vol_colors.append("#78909c")
                else:
                    vol_colors.append("#f03e3e" if p >= prices[i - 1] else "#2f9e44")

            fig_m = make_subplots(
                rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                row_heights=[0.62, 0.38],
                specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
                subplot_titles=("股價 vs 融資餘額", "成交量"),
            )
            # Row 1: 融資餘額 (bar) + 股價 (line, secondary y)
            fig_m.add_trace(go.Bar(
                x=x_labels, y=balances, name="融資餘額 (張)",
                marker_color="#ff9800", opacity=0.6,
            ), row=1, col=1, secondary_y=False)
            if any(p is not None for p in prices):
                fig_m.add_trace(go.Scatter(
                    x=x_labels, y=prices, name="股價 (收盤)",
                    line=dict(color="#74c0fc", width=2.4), mode="lines+markers",
                ), row=1, col=1, secondary_y=True)
            # Row 2: 成交量 (bar, coloured by up/down day)
            if any(v is not None for v in volumes):
                fig_m.add_trace(go.Bar(
                    x=x_labels, y=volumes, name="成交量",
                    marker_color=vol_colors, opacity=0.75, showlegend=False,
                ), row=2, col=1)
            fig_m.update_layout(
                height=340, plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                font=dict(color="#fafafa"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=10, r=10, t=40, b=10), bargap=0.4,
            )
            fig_m.update_yaxes(title_text="融資(張)", showgrid=True,
                               gridcolor="rgba(255,255,255,0.06)", row=1, col=1, secondary_y=False)
            fig_m.update_yaxes(title_text="股價", showgrid=False, row=1, col=1, secondary_y=True)
            fig_m.update_yaxes(title_text="量", showgrid=True,
                               gridcolor="rgba(255,255,255,0.06)", row=2, col=1)
            st.plotly_chart(fig_m, use_container_width=True)
            st.caption(
                "💡 近 20 日趨勢，三者一起看：融資餘額（橘柱）、股價（藍線）、成交量（紅漲/綠跌）。"
                "融資柱狀持續墊高＝散戶加碼槓桿；融資增+量增+價漲＝追高過熱；"
                "融資增+價跌＝出貨套牢；融資柱狀下滑+價穩＝去槓桿、籌碼安定。"
            )

        for rsn in margin_signal["reasons"]:
            icon = "🔴" if "-" in rsn else "🟢" if "+" in rsn else "⚪"
            st.markdown(f"{icon} {rsn}")
        if m_date:
            st.caption(f"資料日期：{m_date}")

    st.markdown("---")
    st.markdown("#### ⚠️ 風險提示")
    for warning in rec["risk_warnings"]:
        st.warning(warning)
    st.info("本系統僅供輔助參考，不構成投資建議。股市有風險，投資需謹慎。")


# ─── Screener ─────────────────────────────────────────────────────────────────

FWD_HORIZONS = (5, 10, 20, 60)


def _forward_returns(df_full, as_of_last):
    """Realized % return from `as_of_last` forward N trading days (and to today)."""
    dates = list(df_full.index)
    try:
        pos = next(i for i, d in enumerate(dates) if d.date() == as_of_last)
    except StopIteration:
        return {}
    closes = df_full["Close"]
    base = float(closes.iloc[pos])
    if not base:
        return {}
    out = {}
    for h in FWD_HORIZONS:
        out[h] = ((float(closes.iloc[pos + h]) / base - 1) * 100
                  if pos + h < len(dates) else None)
    out["today"] = (float(closes.iloc[-1]) / base - 1) * 100
    return out


def _analyze_one_stock(stock_id: str, period: str = None, limit_up_info=None,
                       as_of_date=None, skip_news=False):
    """
    limit_up_info: dict from get_limit_up_stocks(), or None for regular pool stocks.
    as_of_date: when set, everything is computed as of that past date (backtest)
                and realized forward returns are attached.

    Uses the SAME shared core as 個股分析 (services/analysis.py) so both pages
    always agree — see that module for why they used to diverge.
    """
    try:
        df, df_full, _iq, _has_iq = prepare_frame(stock_id, as_of_date=as_of_date)
        if df is None or df.empty or len(df) < 20:
            return None
        info = get_ticker_info(stock_id)
        financials = get_financials(stock_id)

        # Chinese name first (yfinance returns English for TW stocks)
        company_name = resolve_company_name(stock_id, info, limit_up_info)

        a = compute_scores(df, info, financials, stock_id, company_name,
                           as_of_date=as_of_date, skip_news=skip_news)
        tech_score = a["tech_score"]; fund_score = a["fund_score"]; news_score = a["news_score"]
        tp = a["tp"]; rec = a["rec"]; potential = a["potential"]
        margin_signal = a["margin_signal"]; risk_plan = a["risk_plan"]
        fundamentals = a["fundamentals"]

        current_price = df["Close"].iloc[-1]
        prev_price = df["Close"].iloc[-2] if len(df) > 1 else current_price
        change_pct = (current_price - prev_price) / prev_price * 100

        return {
            "stock_id":     stock_id,
            "company_name": company_name,
            "current_price": current_price,
            "change_pct":   change_pct,
            "total_score":  rec["total_score"],
            "action":       rec["action"],
            "action_en":    rec["action_en"],
            "color":        rec["color"],
            "icon":         rec["icon"],
            "tech_score":   tech_score,
            "fund_score":   fund_score,
            "news_score":   news_score,
            "target_price": tp.get("recommended_target"),
            "upside_pct":   tp.get("upside_pct"),
            "target_low":   tp.get("target_low"),
            "target_high":  tp.get("target_high"),
            "pe_ratio":     info.get("trailingPE"),
            "dividend_yield": fundamentals.get("dividend_yield"),  # normalised fraction
            "revenue_growth": info.get("revenueGrowth"),
            # Margin (融資) chip-structure risk
            "margin_usage":   margin_signal.get("usage_pct"),
            "margin_level":   margin_signal.get("level"),
            "margin_penalty": margin_signal.get("penalty", 0),
            # Potential ("sleeper") scoring
            "potential":      potential,
            # Risk plan (ATR stop / target / R:R) — used to filter poor risk-reward
            "rr":             (risk_plan or {}).get("rr"),
            "atr_pct":        (risk_plan or {}).get("atr_pct"),
            "stop_pct":       (risk_plan or {}).get("stop_pct"),
            # Per-horizon scores borrowed from 個股分析 — lets the screener rank by
            # holding period instead of a single blended number.
            "horizon": {h["key"]: {"score": h["score"], "action": h["action"],
                                   "icon": h["icon"], "color": h["color"],
                                   "name": h["name"], "span": h["span"]}
                        for h in a["horizon_cards"]},
            # 純技術週期分：排序與實證對照用（回測驗證的就是它）
            "horizon_tech": {k: v["score"] for k, v in a["horizon_tech"].items()},
            "above_ma120": a.get("above_ma120"),
            "r60": a.get("r60"),
            "overheat": a.get("overheat") or {},
            "trend_score": (a.get("trend") or {}).get("score"),
            "trend_pcts": (a.get("trend") or {}).get("percentiles") or {},
            "trend_why": a.get("trend_why") or [],
            # 量價方向（回測顯示：長線分 + 量價未轉弱 是最佳組合）
            "volume_adj": (a["volume_signal"] or {}).get("score_adj", 0),
            # Backtest only: realized forward returns from the as-of date
            "fwd": _forward_returns(df_full, df.index[-1].date()) if as_of_date else {},
            "as_of_last": df.index[-1].date() if as_of_date else None,
            # Limit-up / streak metadata (None for regular pool)
            "is_limit_up":      limit_up_info is not None,
            "limit_up_pct":     (limit_up_info or {}).get("change_pct"),
            "exchange":         (limit_up_info or {}).get("exchange", "TWSE"),
            "max_streak":       (limit_up_info or {}).get("max_streak", 0),
            "trailing_streak":  (limit_up_info or {}).get("trailing_streak", 0),
            "last_days_ago":    (limit_up_info or {}).get("last_days_ago", 0),
        }
    except Exception:
        return None


def render_smart_screener_page():
    st.title("🎯 智能選股")
    st.markdown(
        "一次掃描、三種策略：**強勢順勢**、**潛力潛伏**、**攻守兼備**。"
        "每檔都同時顯示『綜合評分』（趨勢強弱）與『潛力分』（題材＋尚未起漲），兩種視角並列。"
    )

    strat_label = st.radio(
        "選股策略", STRAT_LABELS, horizontal=True, key="smart_strategy",
        captions=STRAT_CAPTIONS,
    )
    sdef = get_strategy(strat_label)
    strategy = sdef["key"]

    # The limit-up universe is only fetched for the 漲停動能 strategy — that toggle
    # is now part of the strategy itself rather than a separate checkbox.
    include_limit_up = (strategy == "limitup")

    # ── Scan universe: full listed market vs the curated shortlist ────────────
    uc1, uc2 = st.columns([1.6, 2.4])
    with uc1:
        universe_label = st.radio(
            "掃描範圍", ["🌏 全市場：上市+上櫃（完整）", "⭐ 熱門股池（快速）"],
            index=0, key="smart_universe", horizontal=False,
        )
    full_market = universe_label.startswith("🌏")
    with uc2:
        if full_market:
            min_turnover_yi = st.select_slider(
                "流動性門檻（日成交金額）",
                options=[0.1, 0.3, 0.5, 1.0, 2.0, 5.0], value=0.5,
                format_func=lambda v: f"{v} 億",
                key="smart_liq",
                help="成交金額太低的股票買賣不易、滑價大。調低可掃更多冷門股（較慢），調高只看流動性好的。",
            )
            st.caption(
                "🌏 全市場模式：涵蓋**上市＋上櫃約 1,974 檔**，**每一檔**都會實際計算"
                "技術面、量價、低基期位階、估值與風報比（非抽樣粗篩）。"
                "新聞與詳細財報無法批次取得，會在入圍後再補齊。"
                "（融資資料目前僅上市有，上櫃股不計融資扣分。）"
            )
        else:
            min_turnover_yi = 0.0
            st.caption("⭐ 快速模式：只掃 31 檔熱門股（約 1.6% 市場覆蓋率），速度快但看不到中小型潛伏股。")

    col_cfg1, col_cfg2, col_cfg3 = st.columns([1, 1.3, 1.7])
    with col_cfg1:
        top_n = st.selectbox("顯示前 N 名", [5, 10, 15, 20, 30], index=1)
    with col_cfg2:
        # Borrowed from 個股分析: rank by holding horizon instead of one blended score
        # ⚠️ 只有「綜合強勢」的排序真的會用到這個選擇；其他策略各自有固定的排序
        # 依據（族群/長線分/本益比/潛力分/漲停天數），選了也不會改變結果——
        # 實測換週期時卡片順序完全不動。故對那些策略直接停用，避免誤導。
        _hz_applies = sdef["uses_horizon"]
        horizon_label = st.selectbox(
            "排序用的週期評分",
            ["長線 半年+ ✅最強", "中線 1個月+ ✅次強", "綜合（不分週期）",
             "短線 1週內", "極短線 1–3天"],
            index=0, key="smart_horizon", disabled=not _hz_applies,
            help=("**只有「🚀 綜合強勢」策略會用到這個選項**——其他策略各有固定排序依據。\n\n"
                  "回測（179期、持有1個月超額報酬）：長線 +2.62%✅、中線 +1.90%✅、"
                  "短線 +1.29%✅、極短線 +0.23%（不顯著）。越長週期的結構分越有效。"),
        )
        horizon_key = {"長線 半年+ ✅最強": "long", "中線 1個月+ ✅次強": "medium",
                       "綜合（不分週期）": None, "短線 1週內": "short",
                       "極短線 1–3天": "ultra_short"}[horizon_label]
    with col_cfg3:
        regime = get_market_regime()
        if regime.get("regime") != "unknown":
            st.markdown(
                f"<div style='padding-top:26px;font-size:13px;'>大盤環境："
                f"<span style='color:{regime['color']};font-weight:700;'>{regime['label']}</span>"
                f"<span style='color:#78909c;'>　買進門檻 {58 + regime['threshold_adj']}</span></div>",
                unsafe_allow_html=True,
            )

    # ── Backtest evidence for the chosen strategy ─────────────────────────────
    # Ranking rules are cheap to invent and easy to believe; the 139-period
    # backtest is the only thing that says whether they actually worked.
    ev = ev_stats_model(sdef["evidence_model"], hold_days=20,
                        run=sdef.get("evidence_run", "main_3y"))
    ev_v = ev_verdict(ev)
    if ev:
        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {ev_v['color']};border-radius:6px;
            padding:10px 14px;margin:6px 0 10px 0;">
  <span style="color:{ev_v['color']};font-weight:700;">{ev_v['icon']} 回測實證：{ev_v['label']}</span>
  <span style="color:#cfd8dc;font-size:13px;">
    — 近3年139期、持有1個月：組合報酬 <b>{ev['portfolio_return']:+.2f}%</b>
    （扣成本 {ev['net_return']:+.2f}%）、
    <b style="color:{ev_v['color']};">超額報酬 {ev['excess_return']:+.2f}%</b>、
    贏過「隨便買」的期數比率 {ev['beat_benchmark_rate']:.0f}%、t={ev['t_stat']:+.2f}
    {'（統計顯著）' if ev['significant'] else '（不顯著）'}
  </span>
</div>""", unsafe_allow_html=True)
        # ── 依「目前大盤環境」給建議（分環境回測推翻了一刀切的結論）──────────
        cur_reg = regime.get("regime", "neutral")
        rs = ev_regime_model(sdef["evidence_model"], cur_reg)
        if rs:
            good = rs["excess_return"] > 0
            rc = "#4caf50" if good else "#f44336"
            st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {rc};border-radius:6px;
            padding:10px 14px;margin:0 0 10px 0;">
  <span style="color:{rc};font-weight:700;">
    {'✅' if good else '⚠️'} 在目前的「{regime.get('label', '')}」環境下：
    此策略歷史超額報酬 {rs['excess_return']:+.2f}%</span>
  <span style="color:#cfd8dc;font-size:13px;">
    （分環境樣本 {rs['periods']} 期）
    {'——與目前盤勢相符，可考慮採用。' if good else '——在這種盤勢下歷史表現不佳。'}
  </span>
</div>""", unsafe_allow_html=True)

        if not rs or rs["excess_return"] <= 0:
            st.info(
                f"💡 目前大盤為「{regime.get('label', '')}」，此策略在這類環境的歷史表現不佳。"
                "**走查驗證顯示：與其隨盤勢切換策略，不如穩定使用「🏆 長線+量能確認」**"
                "（後90期未參與挑選的測試中，穩定單押 +5.27%／勝率 77.8%，"
                "而依環境切換只有 +3.05%／勝率 54.4%）。"
            )

        if ev["excess_return"] < 0 and ev["significant"]:
            st.warning(
                f"⚠️ **全期間平均而言，此策略輸給「隨便買」**（超額 {ev['excess_return']:+.2f}%，"
                f"{ev['periods']} 期中僅 {ev['beat_benchmark_rate']:.0f}% 贏過大盤）。"
                "分環境檢驗顯示它在**空頭期間轉為有效**（潛力潛伏 +2.23%、攻守兼備 +1.13%），"
                "屬於逆勢型策略；但**走查驗證中，靠盤勢切換到這類策略並未帶來好處**"
                "（+3.05% vs 穩定用長線型 +5.27%）——因為等你確認是空頭，跌勢常已走完一段。"
            )
    elif strategy == "lowpe":
        st.warning(
            "❔ **這個策略我無法給你實證數據——請當作探索工具，不是有依據的建議。**\n\n"
            "我試過用證交所歷史本益比回測，但 `BWIBBU_d` 端點**限流極嚴**"
            "（60 個日期有 43 個被擋，只成功 17 個），樣本太少且非隨機，"
            "所以那份結果我**作廢不採用**。因此「低本益比在台股有沒有效」目前是**未知數**。\n\n"
            "⚠️ 已知風險：低本益比常伴隨**價值陷阱**——便宜是因為獲利即將衰退，"
            "或 EPS 被業外一次性收益灌大（本策略已排除 <3 倍者，但無法完全過濾）。"
            "建議搭配「長線結構分」與營收趨勢一起看，別只看本益比。"
        )
    else:
        st.caption(f"❔ 此策略尚未納入回測驗證（{ev_v['note']}）")

    skip_news = st.checkbox(
        "⚡ 略過新聞分析（掃描快 2–3 倍）", value=False, key="smart_skipnews",
        help=("新聞抓取是掃描最慢的一環（每檔約 6 秒）。新聞面佔綜合評分 15%，"
              "但因為沒有歷史新聞快照，它的貢獻**從未被回測驗證**。"
              "略過後消息面以中性 50 計；長線結構分含 5% 新聞權重，"
              "故會有 1 分內的微小差異（實測 82→81），不影響排序結論。"),
    )

    # 說明文字全部來自策略表（services/strategies.py），不再散落
    _sort_desc = sdef["sort_desc"]
    if sdef["uses_horizon"]:
        _sort_desc = f"**{horizon_label.split()[0]}分**（可用上方選單切換）"
    st.caption(f"📋 {sdef['bar_note']}　｜　排序依據：{_sort_desc}"
               + ("" if sdef["uses_horizon"] else "（此策略不使用上方的週期選單）"))

    # ── 分數門檻實證：幾分以上才值得買 ────────────────────────────────────────
    thr = long_threshold()
    if thr:
        st.info(
            f"🎯 **分數門檻實證**（依 179 期、依分數分桶而非排名）：長線結構分 "
            f"**低於 {thr:.0f} 分時，1個月超額報酬全為負**（-0.06% ~ -0.95%），"
            f"達 **70–75 分為 +1.30%**、80分以上 +0.92%。"
            f"　**持有 3 個月勝率最高（58.7%，超額 +2.61%）**，1個月 53.6%、1週僅 50.2%。"
            f"→ 建議只買 **{thr:.0f} 分以上**並**抱滿 3 個月**。"
        )

    # One shared scan serves all strategies (switching re-ranks the same results
    # with no rescan). Only 漲停動能 uses a different universe, so it caches apart.
    # Scoring always uses the standard 2-year basis (services/analysis.SCORING_PERIOD),
    # so the cache key no longer depends on a display period — only on the universe.
    # 快取鍵刻意**只看掃描範圍**，不含策略、週期或流動性門檻：
    #   · 換策略 = 對同一批結果重新排序，不需重抓
    #   · 調流動性滑桿 = 記憶體過濾（掃描一律以最寬門檻下載）
    #   · 漲停動能 = 全市場模式下這些股本來就掃過了，只需標記，不必另掃
    # 先前把 min_turnover 與 include_limit_up 併進鍵裡，導致這三種操作都會整批重抓。
    uni_tag = "full" if full_market else ("poplu" if include_limit_up else "pop")
    cache_key    = f"smart_{uni_tag}"
    last_run_key = f"smart_time_{uni_tag}"

    col_btn, col_time = st.columns([1, 3])
    with col_btn:
        run_btn = st.button("🔄 重新掃描", type="primary")
    with col_time:
        if last_run_key in st.session_state:
            last_t = st.session_state[last_run_key]
            n = st.session_state.get(cache_key + "_scanned") or \
                len(st.session_state.get(cache_key, []))
            mins = (datetime.datetime.now() - last_t).total_seconds() / 60
            st.caption(
                f"✅ 已快取 {n} 檔（{last_t.strftime('%H:%M:%S')}，{mins:.0f} 分鐘前）　"
                "**切換策略／持有週期／流動性門檻都不會重抓**，只重新排序。"
                "歷史資料另有硬碟快取，重啟後仍在。"
            )
        else:
            st.caption("尚未掃描。掃過一次後，切換策略與調整門檻都不需要重抓資料。")

    # ── Full-market scan: analyse EVERY liquid listed stock, then deep-enrich ──
    if full_market and run_btn:
        prog = st.progress(0.0, text="下載全市場歷史資料...")
        status = st.empty()

        def _cb(done, total):
            prog.progress(min(0.45, 0.45 * done / max(total, 1)),
                          text=f"下載全市場歷史資料 {done}/{total}")

        status.markdown("📥 **第 1 階段**：批次下載全上市股票 2 年日線並計算指標…")
        rows, _snap = scan_universe(min_turnover=min_turnover_yi * 1e8, progress_cb=_cb)
        if not rows:
            prog.empty(); status.empty()
            st.error("全市場掃描失敗（證交所或 Yahoo 資料暫時不可用），請稍後再試或改用熱門股池。")
            return

        # Shortlist on the signals that actually vary in the bulk pass
        prelim_key = sdef["prelim_key"]
        n_enrich = min(int(top_n) * 2 + 6, 40)   # ~5s per deep analysis
        shortlist = sorted(rows, key=lambda r: r.get(prelim_key, 0), reverse=True)[:n_enrich]

        status.markdown(
            f"🔬 **第 2 階段**：已完整分析 **{len(rows)}** 檔上市股，"
            f"為前 **{len(shortlist)}** 名補齊新聞情緒、催化劑與詳細財報…"
        )
        enriched, seen = [], set()
        for i, r in enumerate(shortlist):
            sid = r["stock_id"]
            status.markdown(f"🔬 深度分析 **{sid} {r['company_name']}** ({i+1}/{len(shortlist)})")
            full = _analyze_one_stock(sid, skip_news=skip_news)
            enriched.append(full if full else r)
            seen.add(sid)
            prog.progress(0.45 + 0.55 * (i + 1) / len(shortlist))

        # 全市場已涵蓋所有上市櫃股，漲停股只需「標記」而非另外掃一輪
        try:
            lu_map = {x["stock_id"]: x for x in get_limit_up_stocks(top_n=30)}
            for row in enriched:
                lu = lu_map.get(row.get("stock_id"))
                if lu:
                    row.update({"is_limit_up": True,
                                "limit_up_pct": lu.get("change_pct"),
                                "max_streak": lu.get("max_streak", 0),
                                "last_days_ago": lu.get("last_days_ago", 0),
                                "exchange": lu.get("exchange", row.get("exchange"))})
        except Exception:
            pass

        prog.empty(); status.empty()
        st.session_state[cache_key] = enriched
        st.session_state[cache_key + "_scanned"] = len(rows)
        st.session_state[last_run_key] = datetime.datetime.now()

    # Run analysis if needed
    if (not full_market) and run_btn:

        # ── Build analysis pool ───────────────────────────────────────────────
        # Base pool: predefined popular stocks
        pool = [{"stock_id": sid, "name": nm, "limit_up_info": None}
                for sid, nm in POPULAR_STOCKS.items()]
        base_ids = set(POPULAR_STOCKS.keys())

        # Optional: limit-up supplement
        limit_up_map = {}   # stock_id → limit_up_info dict
        if include_limit_up:
            with st.spinner("抓取今日漲停股清單..."):
                lu_stocks = get_limit_up_stocks(top_n=30)
            if lu_stocks:
                added = 0
                multi_day = sum(1 for s in lu_stocks if s.get("max_streak", 1) >= 2)
                for lu in lu_stocks:
                    sid = lu["stock_id"]
                    if sid not in base_ids:
                        pool.append({"stock_id": sid, "name": lu["name"],
                                     "limit_up_info": lu})
                        base_ids.add(sid)
                        added += 1
                    limit_up_map[sid] = lu   # also mark base-pool stocks that hit limit-up
                max_s_top = max((s.get("max_streak", 1) for s in lu_stocks), default=1)
                st.info(
                    f"🔥 連日漲停動能偵測：共 {len(lu_stocks)} 支有近期漲停紀錄，"
                    f"其中 **{multi_day} 支為連續2天以上**（最長連續 {max_s_top} 天）。"
                    f"新增 {added} 支到分析池。"
                )
            else:
                st.warning("目前無法偵測到連日漲停股（可能是 API 暫時不可用，或市場尚未收盤）")

        # ── Run analysis ──────────────────────────────────────────────────────
        results = []
        progress = st.progress(0.0, text="準備掃描...")
        status = st.empty()
        total = len(pool)

        for i, item in enumerate(pool):
            sid = item["stock_id"]
            name = item["name"] or sid
            lu_info = item["limit_up_info"] or limit_up_map.get(sid)
            status.markdown(
                f"⏳ 分析中 **{sid} {name}** {'🔥' if lu_info else ''} ({i+1}/{total})"
            )
            r = _analyze_one_stock(sid, limit_up_info=lu_info, skip_news=skip_news)
            if r:
                results.append(r)
            progress.progress((i + 1) / total, text=f"{sid} {name} 完成")

        progress.empty()
        status.empty()
        results.sort(key=lambda x: x["total_score"], reverse=True)
        st.session_state[cache_key] = results
        st.session_state[last_run_key] = datetime.datetime.now()

    results = st.session_state.get(cache_key, [])
    if not results:
        st.info(
            "👆 **請按上方「🔄 重新掃描」開始分析**"
            + ("（全市場模式約需 3–5 分鐘）" if full_market else "（熱門股池約需 1–2 分鐘）")
            + "。掃描結果會保留，切換策略或持有週期都**不會重跑**。"
        )
        # 實證資料是靜態參考，尚未掃描時也該看得到
        render_evidence_table()
        return

    # Combined ("攻守兼備") score = geometric mean of momentum & potential — needs
    # BOTH to be decent (a mooned or a weak stock is naturally pulled down).
    for r in results:
        pt = (r.get("potential") or {}).get("total", 0)
        r["combined_score"] = int(round((max(r["total_score"], 0) * max(pt, 0)) ** 0.5))

    # ── Strategy-specific ranking + filtering (over the shared scan) ───────────
    # Each strategy's bar is intrinsic to the strategy (no separate checkboxes).
    buy_bar = 58 + (get_market_regime().get("threshold_adj", 0) or 0)

    # 篩選＋排序完全由策略表決定（services/strategies.py），
    # 不再有一長串 if-elif —— 新增策略只要在表裡加一筆。
    view = strat_select(sdef, results, {"buy_bar": buy_bar, "horizon_key": horizon_key,
                                        "trend_bar": TREND_BUY_BAR})

    # ── Summary strip (always shows both lenses) ──────────────────────────────
    buy_ct     = sum(1 for r in results if r["total_score"] >= 58)
    sleeper_ct = sum(1 for r in results if (r.get("potential") or {}).get("qualifies"))
    lu_ct      = sum(1 for r in results if r.get("is_limit_up"))
    scanned = st.session_state.get(cache_key + "_scanned")
    cols_m = st.columns(4)
    with cols_m[0]:
        if scanned:
            st.metric("完整分析檔數", scanned, "全上市")
        else:
            st.metric("掃描股票數", len(results), "熱門股池")
    with cols_m[1]:
        st.metric(f"買進建議（≥{buy_bar}）", buy_ct, f"/{len(results)} 深analysed".replace("analysed", "析"))
    with cols_m[2]:
        st.metric("潛伏股（題材未漲）", sleeper_ct, "支")
    with cols_m[3]:
        st.metric("符合此策略", len(view), "支")
    if scanned:
        st.caption(
            f"🌏 本次**完整分析了 {scanned} 檔上市股**（技術／量價／低基期／融資／估值／風報比 逐檔實算），"
            f"再對排名最前的 {len(results)} 檔補齊新聞與詳細財報後做最終排序。"
        )

    if not view:
        if strategy == "contrarian":
            st.warning("目前股池中沒有『低基期且有題材』的個股（多頭時本來就稀少）。"
                       "回測顯示這類逆勢策略在多頭失效、空頭才強，找不到標的是正常的。")
        elif strategy == "limitup":
            st.warning("目前偵測不到近期連日漲停的個股（可能 API 暫時不可用，或市場尚未收盤）。")
        else:
            st.warning(f"目前沒有達買進線的個股——大盤為「{regime.get('label','')}」，"
                       "門檻已依環境調整。可改用『🌱 逆勢潛伏』尋找其他機會。")
        return

    display = view[:top_n]

    metric_of = sdef["metric"]
    # 圖表標題＝排序依據的白話名稱（去掉 markdown 粗體與括號說明）
    metric_name = sdef["sort_desc"].replace("**", "").split("（")[0]
    if sdef["uses_horizon"]:
        metric_name = f"{horizon_label.split()[0]} 評分"

    st.markdown("---")
    st.markdown(f"### 依「{strat_label}」排序 — 前 {len(display)} 名")

    # Score distribution chart (of the strategy's primary metric)
    if len(display) > 0:
        primary_color = sdef["color"]
        bar_colors = [r["color"] for r in display] if primary_color is None \
            else [primary_color] * len(display)
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=[f"{r['stock_id']}<br>{r['company_name']}" for r in display],
            y=[metric_of(r) for r in display],
            marker_color=bar_colors,
            text=[f"{metric_of(r)}" for r in display],
            textposition="outside",
            textfont=dict(color="#fafafa"),
        ))
        if strategy in ("trend", "sectorhot", "limitup"):
            fig_bar.add_hline(y=buy_bar + 10, line_dash="dot", line_color="#4caf50",
                              annotation_text=f"強力買進線 {buy_bar + 10}", annotation_position="right")
            fig_bar.add_hline(y=buy_bar, line_dash="dot", line_color="#a9e34b",
                              annotation_text=f"買進線 {buy_bar}", annotation_position="right")
            fig_bar.add_hline(y=buy_bar - 10, line_dash="dot", line_color="#ff9800",
                              annotation_text=f"觀望線 {buy_bar - 10}", annotation_position="right")
        fig_bar.update_layout(
            height=300, title=dict(text=metric_name, font=dict(size=13, color="#aaa")),
            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
            font=dict(color="#fafafa"),
            yaxis=dict(range=[0, 105], showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
            xaxis=dict(showgrid=False),
            margin=dict(l=10, r=120, t=30, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    # Unified dual-score cards
    for rank, r in enumerate(display, 1):
        _render_smart_card(rank, r, strategy, horizon_key)

    st.markdown("---")
    if strategy == "contrarian":
        st.caption("⚠️ 逆勢潛伏本質是「還沒漲」，可能長期沉潛或題材落空。"
                   "回測：多頭 −0.98%、空頭 +2.23% —— 只在空頭有優勢，多頭請避開。")
    elif strategy == "lowpe":
        st.caption("⚠️ 139期回測顯示**單純買最低本益比顯著虧錢**"
                   "（持有3個月超額 −4.48%、t=−3.82；近一年更達 −18.25%）。"
                   "低本益比多半反映衰退預期而非便宜。此頁供你自行研判，不是推薦。")
    elif strategy == "limitup":
        st.caption("⚠️ 漲停股波動極大、籌碼凌亂，追高風險高。請務必參考卡片上的風報比與停損幅度，嚴格控管部位。")
    else:
        st.caption("⚠️ 評分模型為量化指標的加權組合，不代表投資建議。請結合個人判斷與風險承受能力做決策。")

    # ── 題材/族群輪動 ─────────────────────────────────────────────────────────
    render_sector_view(results)

    # ── Stored backtest evidence (all models) ─────────────────────────────────
    render_evidence_table()

    # ── Strategy backtest ─────────────────────────────────────────────────────
    render_strategy_backtest(strategy, strat_label, horizon_key, horizon_label,
                             include_limit_up, top_n, buy_bar)


# ─── 題材／族群輪動 ───────────────────────────────────────────────────────────

def render_sector_view(results):
    """哪些族群正在發動，以及族群內的個股分布。"""
    if not results:
        return
    st.markdown("---")
    with st.expander("🏭 題材族群輪動：哪個族群正在發動？", expanded=False):
        sectors = analyse_sectors(results, min_members=3)
        if not sectors:
            st.info("掃描檔數不足以做族群分析（每個族群至少需 3 檔）。建議用全市場掃描。")
            return

        st.caption(
            "族群動能＝該族群成員近 60 日報酬的**中位數**（用中位數避免被單一飆股拉高）；"
            "廣度＝族群內上漲家數比率。依證交所／櫃買官方「產業別」分類。"
        )
        st.success(
            "✅ **實證：買「強勢族群裡的強股」有效**——只在前 5 強族群裡挑長線分最高者，"
            "1個月超額 **+3.07%**（t=5.32）、3個月 **+10.59%**（t=6.94），"
            "**優於不分族群的預設策略**（+2.58% / +9.34%）。"
        )
        st.error(
            "⛔ **但「補漲」是錯覺**——「強勢族群裡還沒跟上的落後股」1個月超額 **−0.25%**、"
            "3個月 **−0.88%**，贏基準率僅 36–40%。**族群強不代表落後股會補漲**，"
            "落後通常有它落後的理由。（對照組「弱勢族群的落後股」更差：−1.38%，t=−3.27）"
        )

        rows_html = []
        for i, s in enumerate(sectors[:14], 1):
            mc = "#f03e3e" if s["mom"] > 0 else "#2f9e44"
            bc = "#4caf50" if s["breadth"] >= 60 else "#ff9800" if s["breadth"] >= 40 else "#f44336"
            hot = "🔥" if i <= 5 else ""
            ml = f"{s['median_long']:.0f}" if s["median_long"] is not None else "—"
            rows_html.append(
                f"<tr style='border-bottom:1px solid #2d3548;'>"
                f"<td style='padding:6px 10px;'>{i} {hot}</td>"
                f"<td style='padding:6px 10px;font-weight:700;'>{s['name']}</td>"
                f"<td style='padding:6px 10px;'>{s['n']}</td>"
                f"<td style='padding:6px 10px;color:{mc};font-weight:800;'>{s['mom']:+.1f}%</td>"
                f"<td style='padding:6px 10px;color:{bc};'>{s['breadth']:.0f}%</td>"
                f"<td style='padding:6px 10px;'>{ml}</td></tr>"
            )
        st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:13px;background:#161b26;border-radius:8px;">
  <thead><tr style="background:#1e2130;color:#b0bec5;font-size:12px;">
    <th style="padding:8px 10px;text-align:left;">排名</th>
    <th style="padding:8px 10px;text-align:left;">族群</th>
    <th style="padding:8px 10px;text-align:left;">檔數</th>
    <th style="padding:8px 10px;text-align:left;">60日動能(中位)</th>
    <th style="padding:8px 10px;text-align:left;">上漲廣度</th>
    <th style="padding:8px 10px;text-align:left;">長線分(中位)</th>
  </tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>""",
                    unsafe_allow_html=True)

        # 前 5 強族群裡，長線分最高的個股（這才是實證有效的做法）
        st.markdown("#### 🔥 前 5 強族群中，長線分最高的個股")
        st.caption("這是實證有效的做法：族群動能 + 個股也強，而**非**挑落後股。")
        hot_ids = {s["name"] for s in sectors[:5]}
        ind = get_industry_map()
        picks = []
        for r in results:
            meta = ind.get(r.get("stock_id"))
            if meta and meta["name"] in hot_ids:
                lg = (r.get("horizon") or {}).get("long", {}).get("score", 0)
                picks.append((lg, meta["name"], r))
        picks.sort(key=lambda x: -x[0])
        if picks:
            for lg, sec, r in picks[:10]:
                p = r.get("potential") or {}
                pe = r.get("pe_ratio")
                st.markdown(
                    f"- **{r['stock_id']} {r['company_name']}**"
                    f"　`{sec}`　長線分 **{lg}**"
                    f"　52週位階 {p.get('position_pct', 0):.0f}%"
                    + (f"　本益比 {pe:.1f}" if pe else "")
                )
        else:
            st.info("目前掃描結果中沒有屬於前 5 強族群的個股。")


# ─── Stored backtest evidence ─────────────────────────────────────────────────

def render_evidence_table():
    """Show the persisted multi-period backtest so strategy choice is informed."""
    meta = ev_meta()
    if not meta:
        return
    st.markdown("---")
    with st.expander("📚 各模型歷史實證（近3年 139 期滾動回測）", expanded=False):
        st.caption(
            f"**方法**：{meta.get('method', '')}　**樣本**：{meta.get('universe', '')}　"
            f"**基準**：{meta.get('benchmark', '')}"
        )
        st.info(
            "🔑 **關鍵在「超額報酬」而非「報酬」**——多頭時什麼都在漲，只有超額報酬"
            "（贏過「隨便買全部」多少）才證明選股排序有價值。t值≥1.96 才代表不是運氣。"
        )
        hold_label = st.radio("持有期間", ["1週", "1個月", "3個月"], index=1,
                              horizontal=True, key="ev_hold")
        hd = {"1週": 5, "1個月": 20, "3個月": 60}[hold_label]
        rows = ev_rows(hd)
        bench = ev_bench(hd)
        if bench is not None:
            st.markdown(f"**基準（買進全部合格股票的等權報酬）：{bench:+.2f}%**")

        html = []
        for r in rows:
            v = ev_verdict(r)
            html.append(
                f"<tr style='border-bottom:1px solid #2d3548;'>"
                f"<td style='padding:6px 10px;font-weight:700;'>{r['name']}</td>"
                f"<td style='padding:6px 10px;'>{r['portfolio_return']:+.2f}%</td>"
                f"<td style='padding:6px 10px;'>{r['net_return']:+.2f}%</td>"
                f"<td style='padding:6px 10px;font-weight:800;color:{v['color']};'>"
                f"{r['excess_return']:+.2f}%</td>"
                f"<td style='padding:6px 10px;'>{r['stock_win_rate']:.1f}%</td>"
                f"<td style='padding:6px 10px;'>{r['beat_benchmark_rate']:.1f}%</td>"
                f"<td style='padding:6px 10px;'>{r['t_stat']:+.2f}"
                f"{'*' if r['significant'] else ''}</td>"
                f"<td style='padding:6px 10px;color:{v['color']};'>{v['icon']} {v['label']}</td>"
                f"</tr>"
            )
        st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:13px;background:#161b26;border-radius:8px;">
  <thead><tr style="background:#1e2130;color:#b0bec5;font-size:12px;">
    <th style="padding:8px 10px;text-align:left;">模型</th>
    <th style="padding:8px 10px;text-align:left;">組合報酬</th>
    <th style="padding:8px 10px;text-align:left;">扣成本後</th>
    <th style="padding:8px 10px;text-align:left;">超額報酬</th>
    <th style="padding:8px 10px;text-align:left;">個股勝率</th>
    <th style="padding:8px 10px;text-align:left;">贏基準率</th>
    <th style="padding:8px 10px;text-align:left;">t值</th>
    <th style="padding:8px 10px;text-align:left;">判定</th>
  </tr></thead>
  <tbody>{''.join(html)}</tbody>
</table></div>""", unsafe_allow_html=True)

        st.markdown("**⚠️ 限制（務必一起看）**")
        for c in meta.get("caveats", []):
            st.markdown(f"　• {c}")
        st.caption(
            f"未納入驗證的訊號：{meta.get('signals_excluded', '')}　"
            f"｜ 重跑指令：`python3 backtest_research.py --stocks 450 --every 5 --years 3 --topn 10`"
        )


# ─── Strategy backtest ────────────────────────────────────────────────────────

def _select_by_strategy(results, strategy, horizon_key, buy_bar, top_n):
    """Apply the same ranking/filtering the live screener uses (共用同一份條件)。"""
    sdef = get_strategy(strategy)
    if sdef:
        for r in results:
            pt = (r.get("potential") or {}).get("total", 0)
            r["combined_score"] = int(round((max(r.get("total_score", 0), 0) * max(pt, 0)) ** 0.5))
        return strat_select(sdef, results,
                            {"buy_bar": buy_bar, "horizon_key": horizon_key,
                             "trend_bar": TREND_BUY_BAR})[:top_n]
    # 策略表是唯一來源；找不到策略就明講，不要退回一份會與它不一致的舊邏輯
    raise KeyError(f"未知策略 {strategy}（請在 services/strategies.py 定義）")

    def hs(r):
        if not horizon_key:
            return r["total_score"]
        return (r.get("horizon") or {}).get(horizon_key, {}).get("score", r["total_score"])

    if strategy == "momentum":
        v = [r for r in results if hs(r) >= buy_bar]; v.sort(key=hs, reverse=True)
    elif strategy == "limitup":
        v = [r for r in results if r.get("is_limit_up")]
        v.sort(key=lambda r: (r.get("max_streak", 0), r["total_score"]), reverse=True)
    elif strategy == "sleeper":
        v = [r for r in results if (r.get("potential") or {}).get("qualifies")]
        v.sort(key=lambda r: (r.get("potential") or {}).get("total", 0), reverse=True)
    else:
        v = [r for r in results
             if r["total_score"] >= 48
             and (r.get("potential") or {}).get("low_base", 0) >= 45
             and (r.get("potential") or {}).get("total", 0) >= 45
             and (r.get("rr") is None or r["rr"] >= 1.5)]
        v.sort(key=lambda r: r["combined_score"], reverse=True)
    return v[:top_n]


def render_strategy_backtest(strategy, strat_label, horizon_key, horizon_label,
                             include_limit_up, top_n, buy_bar):
    st.markdown("---")
    with st.expander("🔍 策略回測 — 這套選股邏輯到底準不準？", expanded=False):
        st.caption(
            "回到過去某一天跑同一套選股，看**當時選出的前 N 名**後來實際漲跌，"
            "並和「大盤」及「全池平均」比較。關鍵指標是 **選股超額報酬 = 前N名平均 − 全池平均**"
            "——若接近 0，代表排序其實沒帶來價值。"
        )
        st.warning(
            "⚠️ **前視偏誤（look-ahead bias）誠實揭露**：技術面、量價、融資、大盤環境皆能"
            "還原到基準日；但**基本面與新聞情緒因免費資料源限制，用的仍是『現在』的數值**。"
            "因此含基本面／消息成分的策略（尤其中長線）結果會偏樂觀。"
            "選『極短線／短線』週期時技術面佔比最高，結果最接近真實。"
        )

        today = datetime.date.today()
        bc1, bc2, bc3 = st.columns([1.2, 1, 1])
        with bc1:
            bt_date = st.date_input(
                "回測基準日", value=today - datetime.timedelta(days=90),
                min_value=today - datetime.timedelta(days=730),
                max_value=today - datetime.timedelta(days=7),
                key="bt_date",
            )
        with bc2:
            hold_days = st.selectbox(
                "持有天數（交易日）", FWD_HORIZONS, index=2, key="bt_hold",
                format_func=lambda d: f"{d} 日" + {5: "（約1週）", 10: "（約2週）",
                                                   20: "（約1個月）", 60: "（約3個月）"}[d],
            )
        with bc3:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            run_bt = st.button("▶️ 執行回測", type="primary", key="bt_run")

        bt_key = f"bt_{bt_date}_lu{int(include_limit_up)}"
        if run_bt:
            pool = [{"stock_id": sid, "name": nm, "limit_up_info": None}
                    for sid, nm in POPULAR_STOCKS.items()]
            results = []
            prog = st.progress(0.0, text="回測掃描中...")
            status = st.empty()
            for i, item in enumerate(pool):
                sid = item["stock_id"]
                status.markdown(f"⏳ 還原 **{sid} {item['name']}** 於 {bt_date} 的狀態 "
                                f"({i + 1}/{len(pool)})")
                r = _analyze_one_stock(sid, limit_up_info=None, as_of_date=bt_date)
                if r and r.get("fwd"):
                    results.append(r)
                prog.progress((i + 1) / len(pool))
            prog.empty(); status.empty()
            st.session_state[bt_key] = results

        results = st.session_state.get(bt_key)
        if not results:
            st.info("設定基準日與持有天數後，點「執行回測」。")
            return

        picks = _select_by_strategy([dict(r) for r in results], strategy,
                                    horizon_key, buy_bar, top_n)
        if not picks:
            st.warning(f"以「{strat_label}」在 {bt_date} 當天選不出任何標的（門檻未達）。"
                       "可換策略或換基準日再試。")
            return

        def fwd(r):
            return (r.get("fwd") or {}).get(hold_days)

        picked = [fwd(r) for r in picks if fwd(r) is not None]
        allp = [fwd(r) for r in results if fwd(r) is not None]
        if not picked or not allp:
            st.warning(f"基準日距今不足 {hold_days} 個交易日，無法驗證該持有期間。請選更早的日期。")
            return

        avg_pick = sum(picked) / len(picked)
        avg_all = sum(allp) / len(allp)
        wins = sum(1 for x in picked if x > 0)
        win_rate = wins / len(picked) * 100
        edge = avg_pick - avg_all
        bench = get_index_forward_return(bt_date.strftime("%Y-%m-%d"), hold_days)
        bench_ret = bench.get("ret")

        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("前N名平均報酬", f"{avg_pick:+.2f}%")
        with m2:
            st.metric("全池平均報酬", f"{avg_all:+.2f}%")
        with m3:
            st.metric("選股超額報酬", f"{edge:+.2f}%",
                      "排序有效" if edge > 0.5 else "排序無明顯優勢" if edge > -0.5 else "排序反效果")
        with m4:
            st.metric("上漲比率", f"{win_rate:.0f}%", f"{wins}/{len(picked)} 檔")
        with m5:
            st.metric("大盤同期", f"{bench_ret:+.2f}%" if bench_ret is not None else "N/A",
                      f"超越 {avg_pick - bench_ret:+.2f}%" if bench_ret is not None else None)

        verdict_color = "#4caf50" if edge > 0.5 else "#ff9800" if edge > -0.5 else "#f44336"
        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {verdict_color};border-radius:6px;
            padding:10px 14px;margin:8px 0;">
  <span style="color:{verdict_color};font-weight:700;">結論</span>
  <span style="color:#cfd8dc;font-size:13px;">
    以「{strat_label}／{horizon_label}」在 {bt_date} 選出 {len(picks)} 檔，持有 {hold_days} 個交易日，
    平均 {avg_pick:+.2f}%，全池平均 {avg_all:+.2f}%，
    <b>選股超額報酬 {edge:+.2f}%</b>
    {f'；同期大盤 {bench_ret:+.2f}%' if bench_ret is not None else ''}。
  </span>
</div>""", unsafe_allow_html=True)

        rows = []
        for i, r in enumerate(picks, 1):
            f = fwd(r)
            if f is None:
                continue
            c = "#4caf50" if f >= 0 else "#f44336"
            rows.append(
                f"<tr style='border-bottom:1px solid #2d3548;'>"
                f"<td style='padding:6px 10px;'>{i}</td>"
                f"<td style='padding:6px 10px;font-weight:700;'>{r['stock_id']} {r['company_name']}</td>"
                f"<td style='padding:6px 10px;'>{r['total_score']}</td>"
                f"<td style='padding:6px 10px;'>{(r.get('potential') or {}).get('total', 0)}</td>"
                f"<td style='padding:6px 10px;color:{c};font-weight:800;'>{f:+.2f}%</td>"
                f"<td style='padding:6px 10px;color:{'#4caf50' if f > avg_all else '#f44336'};'>"
                f"{f - avg_all:+.2f}%</td></tr>"
            )
        st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:13px;background:#161b26;border-radius:8px;">
  <thead><tr style="background:#1e2130;color:#b0bec5;font-size:12px;">
    <th style="padding:8px 10px;text-align:left;">#</th>
    <th style="padding:8px 10px;text-align:left;">股票</th>
    <th style="padding:8px 10px;text-align:left;">當時綜合</th>
    <th style="padding:8px 10px;text-align:left;">當時潛力</th>
    <th style="padding:8px 10px;text-align:left;">持有{hold_days}日報酬</th>
    <th style="padding:8px 10px;text-align:left;">相對全池</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table></div>""", unsafe_allow_html=True)
        st.caption(
            "⚠️ 單一時點的回測樣本極小，結果受個股與當時盤勢影響很大，"
            "**不足以證明策略長期有效**。建議多換幾個基準日與持有天數交叉驗證。"
        )


# ─── Smart-screener unified card ──────────────────────────────────────────────

def _score_cell(label, value, color, primary):
    """One score column; the strategy's primary metric gets a coloured ring."""
    ring = (f"box-shadow:0 0 0 2px {color};border-radius:10px;background:rgba(255,255,255,0.03);"
            if primary else "")
    star = " ★" if primary else ""
    return (
        f"<div style='min-width:76px;text-align:center;padding:6px 8px;{ring}'>"
        f"<div style='font-size:26px;font-weight:900;color:{color};line-height:1;'>{value}</div>"
        f"<div style='font-size:10px;color:#aaa;margin-top:2px;'>{label}{star}</div></div>"
    )


def _render_smart_card(rank, r, strategy, horizon_key=None):
    p = r.get("potential") or {}
    color = r["color"]; icon = r["icon"]; action = r["action"]
    current = r["current_price"]; chg = r["change_pct"]
    chg_color = "#f03e3e" if chg >= 0 else "#2f9e44"
    upside = r.get("upside_pct")
    up_color = "#4caf50" if (upside or 0) >= 0 else "#f44336"
    upside_str = f"{upside:+.1f}%" if upside is not None else "N/A"
    pos = p.get("position_pct")
    pos_val = pos if pos is not None else 50
    r60 = p.get("r60")
    r60_str = f"近60日 {r60:+.0f}%" if r60 is not None else ""
    qualifies = p.get("qualifies")
    pot_total = p.get("total", 0)
    comb = r.get("combined_score", 0)
    t_w = r["tech_score"]; f_w = r["fund_score"]; n_w = r["news_score"]
    rank_emoji = ["🥇", "🥈", "🥉"][rank - 1] if rank <= 3 else f"#{rank}"

    # Limit-up badge
    lu_badge = ""
    if r.get("is_limit_up"):
        exch = r.get("exchange", "TWSE")
        max_streak = r.get("max_streak", 0)
        last_ago = r.get("last_days_ago", 0)
        streak_label = f"{'🔥' * min(max_streak, 5)} 連{max_streak}日" if max_streak >= 2 else "🔥 漲停"
        when_label = {0: "今日", 1: "昨日"}.get(last_ago, f"{last_ago}日前" if last_ago <= 3 else "近期")
        lu_pct = r.get("limit_up_pct")
        pct_str = f" +{lu_pct:.1f}%" if lu_pct else ""
        lu_badge = (f"<span style='font-size:11px;background:#7f1d1d;color:#fca5a5;border-radius:4px;"
                    f"padding:2px 6px;margin-left:6px;white-space:nowrap;'>{streak_label} ({when_label}{pct_str}) · {exch}</span>")

    # Margin badge
    margin_badge = ""
    m_usage = r.get("margin_usage"); m_level = r.get("margin_level")
    if m_usage is not None and m_level in ("elevated", "high"):
        mb_color = "#f44336" if m_level == "high" else "#ff9800"
        margin_badge = (f"<span style='font-size:11px;background:#3a2410;color:{mb_color};"
                        f"border:1px solid {mb_color};border-radius:4px;padding:1px 6px;"
                        f"margin-left:6px;white-space:nowrap;'>💳 融資 {m_usage:.0f}%</span>")

    # Sleeper tag
    sleeper_badge = ("<span style='font-size:11px;background:#1a237e;color:#9fa8da;border-radius:4px;"
                     "padding:1px 6px;margin-left:6px;white-space:nowrap;'>🌱 潛伏</span>") if qualifies else ""

    # Three always-visible scores; primary highlighted per strategy
    _ts = r.get("trend_score")
    cell_momentum = _score_cell("趨勢分" if _ts is not None else "綜合",
                                f"{_ts:.0f}" if _ts is not None else r["total_score"],
                                color, strategy in ("trend", "sectorhot", "limitup"))
    cell_sleeper  = _score_cell("潛力", pot_total, "#7986cb", strategy == "contrarian")
    cell_balanced = _score_cell("綜合", r["total_score"], "#4dd0e1", False)

    # Risk/reward (from the ATR plan) — shows whether the entry is worth the risk
    rr = r.get("rr")
    if rr is not None:
        rr_color = "#4caf50" if rr >= 2.5 else "#a9e34b" if rr >= 1.5 else "#ff9800" if rr >= 1 else "#f44336"
        stop_pct = r.get("stop_pct")
        rr_html = (
            f"<div style='min-width:96px;font-size:11px;color:#aaa;'>"
            f"<div>風報比 <span style='color:{rr_color};font-weight:800;font-size:14px;'>{rr:.2f}</span></div>"
            f"<div>停損 {abs(stop_pct):.1f}%　ATR {r.get('atr_pct', 0):.1f}%</div></div>"
        )
    else:
        rr_html = "<div style='min-width:96px;'></div>"

    # 本益比（共用元件，三頁一致）
    pe_html = pe_badge(r.get("pe_ratio"), r.get("dividend_yield"),
                       highlight=(strategy == "lowpe"))

    # 四格週期分數（共用元件）
    horizon_html = horizon_cells(r.get("horizon"), selected_key=horizon_key)
    rr_html = rr_cell(r.get("rr"), r.get("stop_pct"), r.get("atr_pct"))

    card_border = "border:2px solid #7f1d1d;" if r.get("is_limit_up") else "border:1px solid #2d3548;"

    with st.container():
        st.markdown(f"""
<div class="stock-row" style="{card_border}">
  <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
    <div style="min-width:46px;text-align:center;">
      <div style="font-size:22px;">{rank_emoji}</div>
    </div>
    <div style="min-width:150px;">
      <div style="font-size:17px;font-weight:900;">{r['stock_id']}
        <span style="font-size:13px;font-weight:600;color:#ccc;">{r['company_name']}</span></div>
      <div style="font-size:12px;">{overheat_badge(r.get("overheat"))}{lu_badge}{margin_badge}{sleeper_badge}</div>
    </div>
    <div style="min-width:78px;">
      <div style="font-size:15px;font-weight:700;">TWD {current:.2f}</div>
      <div style="font-size:12px;color:{chg_color};">{chg:+.2f}%</div>
    </div>
    {cell_momentum}
    <div style="min-width:60px;font-size:11px;color:{color};text-align:center;">{icon}<br>{action}</div>
    {cell_sleeper}
    {cell_balanced}
    <!-- 52w position + target -->
    <div style="min-width:150px;">
      <div style="font-size:11px;color:#90a4ae;">52週位置 {pos_val:.0f}%　<span style="color:#78909c;">{r60_str}</span></div>
      <div style="background:#2d3548;height:8px;border-radius:4px;position:relative;margin:3px 0;">
        <div style="position:absolute;left:{max(0,min(100,pos_val)):.0f}%;top:-2px;transform:translateX(-50%);
                    width:3px;height:12px;background:#7986cb;border-radius:2px;"></div>
      </div>
      <div style="font-size:12px;">目標 <span style="color:{up_color};font-weight:700;">{upside_str}</span></div>
    </div>
    {pe_html}
    {horizon_html}
    {rr_html}
    <!-- tech/fund/news mini -->
    <div style="min-width:120px;font-size:11px;color:#aaa;">
      <div>技 <span style="color:#74c0fc;font-weight:700;">{t_w}</span>
           基 <span style="color:#a9e34b;font-weight:700;">{f_w}</span>
           息 <span style="color:#ffd43b;font-weight:700;">{n_w}</span></div>
      <div style="display:flex;gap:2px;margin-top:3px;">
        <div style="background:#74c0fc;width:{t_w*1.2:.0f}px;height:5px;border-radius:2px;"></div>
        <div style="background:#a9e34b;width:{f_w*1.2:.0f}px;height:5px;border-radius:2px;"></div>
        <div style="background:#ffd43b;width:{n_w*1.2:.0f}px;height:5px;border-radius:2px;"></div>
      </div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        # Strategy-relevant "why" expander
        with st.expander(f"為什麼 {r['stock_id']} {r['company_name']}？", expanded=False):
            summary = p.get("summary", "")
            if summary:
                st.markdown(f"🌱 **潛力面**：{summary}")
            ec1, ec2 = st.columns(2)
            with ec1:
                st.markdown("**📰 話題**")
                for x in p.get("buzz_reasons", []) or ["—"]:
                    st.markdown(f"　• {x}")
                st.markdown("**🚀 前瞻（成長）**")
                for x in p.get("prospect_reasons", []) or ["成長數據有限"]:
                    st.markdown(f"　• {x}")
            with ec2:
                st.markdown("**💰 潛力（目標空間）**")
                for x in p.get("upside_reasons", []) or ["目標價資料有限"]:
                    st.markdown(f"　• {x}")
                st.markdown("**🌱 低基期（是否還沒漲）**")
                for x in p.get("low_base_reasons", []) or ["—"]:
                    st.markdown(f"　• {x}")

        if st.button(f"📊 詳細分析 {r['stock_id']} {r['company_name']}",
                     key=f"smart_goto_{r['stock_id']}_{rank}"):
            st.session_state["stock_id"] = r["stock_id"]
            st.session_state["_nav_to"] = "📊 個股分析"
            st.rerun()


# ─── Portfolio (我的持股) ─────────────────────────────────────────────────────

def render_portfolio_page():
    st.title("💼 我的持股")
    st.markdown(
        "把**實際持有的部位**和**系統評分**放在一起看：一眼掌握每檔的排名、評分、"
        "投資建議與未實現損益，並找出「你持有、但系統轉為偏空」的警訊部位。"
    )

    # 依回測結論排序：長線分是唯一穩健有效的訊號（超額+3.10%, t=4.75），
    # 綜合評分只是弱有效（+0.81%, t=2.04），故預設用長線分排名。
    ev_long = ev_stats("momentum", "long", 20)
    ev_total = ev_stats("momentum", None, 20)
    rank_label = st.radio(
        "排名依據",
        ["🏆 趨勢結構分（回測最強）", "綜合評分"],
        horizontal=True, key="pf_rank_by",
        captions=[
            (f"回測超額 {ev_long['excess_return']:+.2f}%、t={ev_long['t_stat']:+.2f}、"
             f"贏大盤率 {ev_long['beat_benchmark_rate']:.0f}%" if ev_long else "回測最強訊號"),
            (f"回測超額 {ev_total['excess_return']:+.2f}%、t={ev_total['t_stat']:+.2f}"
             if ev_total else "四面向加權"),
        ],
    )
    rank_by_long = rank_label.startswith("🏆")
    with st.expander("📖 這些分數與門檻是什麼意思？（實證說明）", expanded=False):
        st.markdown("""
**為什麼預設用「長線結構分」而非「綜合評分」？**
179 期滾動回測顯示，長線結構分的選股能力遠強於綜合評分
（超額報酬 **+3.10% vs +0.81%**），且在多頭／震盪／空頭三種環境都是正的。

**「超額報酬」是什麼？為什麼看它而不是報酬率？**
超額報酬 =（這批股票的報酬）−（當天全市場等權平均報酬）。
多頭時什麼都在漲，看絕對報酬會誤以為模型很神；**只有贏過「隨便買」才證明選股有價值**。

**70 分門檻怎麼來的？**
把所有個股**依分數分桶**（不是排名），統計每個區間後續的實際表現：

| 長線分區間 | 1個月超額報酬 |
|---|---|
| 低於 70 分 | **全部為負**（−0.06% ~ −0.95%）|
| 70–75 分 | **+1.30%** ✅ |
| 80 分以上 | **+0.92%** ✅ |

所以 70 分是分水嶺——**低於 70 分的股票，歷史上買了平均跑輸大盤**。

**為什麼建議抱 3 個月？**
同樣一批 70 分以上的股票，持有越久表現越好：

| 持有 | 勝率 | 扣成本後報酬 | 超額報酬 |
|---|---|---|---|
| 1 週 | 50.2% | +0.38% | +0.30% |
| 1 個月 | 53.6% | +3.23% | +0.94% |
| **3 個月** | **58.7%** | **+10.79%** | **+2.61%** |

1 週幾乎等於丟銅板（50.2%），而且頻繁進出還要一直付 0.585% 的手續費與證交稅。

⚠️ **限制**：測試期含多頭124/震盪28/空頭27期，空頭樣本較少；且有存活者偏誤
（已下市公司不在樣本內）。詳見 `BACKTEST_FINDINGS.md`。
""")

    holdings = load_holdings()

    # ── Add / edit form ───────────────────────────────────────────────────────
    with st.expander("➕ 新增／修改持股", expanded=not holdings):
        f1, f2, f3, f4 = st.columns([1.4, 1, 1, 0.8])
        with f1:
            in_query = st.text_input("股票代碼或中文名稱", placeholder="例如 2330 或 台積電",
                                     key="pf_query")
        with f2:
            in_shares = st.number_input("股數", min_value=0.0, step=1000.0, value=1000.0,
                                        key="pf_shares")
        with f3:
            in_cost = st.number_input("成本價 (TWD)", min_value=0.0, step=1.0, value=0.0,
                                      key="pf_cost")
        with f4:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            add_btn = st.button("儲存", type="primary", key="pf_add")

        if add_btn:
            res = resolve_query(in_query)
            if not res["matched"]:
                st.error(f"查無「{in_query}」，請確認代碼或名稱。")
            elif in_shares <= 0 or in_cost <= 0:
                st.error("股數與成本價都必須大於 0。")
            else:
                sid = res["stock_id"]
                upsert_holding(sid, display_name(sid), in_shares, in_cost)
                st.success(f"已儲存 {sid} {display_name(sid)}：{in_shares:,.0f} 股 @ {in_cost:,.2f}")
                st.rerun()

    # ── 自選股整合：把「觀察中」和「持有中」放在同一頁 ────────────────────────
    watch = [w for w in load_watchlist()
             if w["stock_id"] not in {h["stock_id"] for h in holdings}]
    if watch:
        with st.expander(f"⭐ 觀察中的自選股（{len(watch)} 檔，尚未持有）", expanded=False):
            st.caption("自選股只是追蹤清單；要納入損益與部位分析，請用下方「轉為持股」。")
            for w in watch:
                wc1, wc2, wc3 = st.columns([3, 1.2, 1])
                with wc1:
                    st.markdown(f"**{w['stock_id']} {w.get('name', '')}**")
                with wc2:
                    if st.button("📊 詳細分析", key=f"wl2_go_{w['stock_id']}"):
                        st.session_state["stock_id"] = w["stock_id"]
                        st.session_state["_nav_to"] = "📊 個股分析"
                        st.rerun()
                with wc3:
                    if st.button("➕ 轉為持股", key=f"wl2_add_{w['stock_id']}"):
                        st.session_state["pf_prefill"] = w["stock_id"]
                        st.rerun()
            pre = st.session_state.get("pf_prefill")
            if pre:
                st.info(f"請在上方「➕ 新增／修改持股」表單輸入 **{pre}** 的股數與成本價。")

    if not holdings:
        st.info("目前沒有持股紀錄。用上方表單新增第一筆吧！")
        return

    # ── Analyse each holding (cached until refresh) ───────────────────────────
    ids = ",".join(sorted(h["stock_id"] for h in holdings))
    cache_key = f"pf_scores_{ids}"
    c1, c2 = st.columns([1, 3])
    with c1:
        refresh = st.button("🔄 重新評分", key="pf_refresh")
    with c2:
        if f"{cache_key}_time" in st.session_state:
            st.caption(f"上次評分：{st.session_state[f'{cache_key}_time'].strftime('%H:%M:%S')}")

    if refresh:
        scores = {}
        prog = st.progress(0.0)
        status = st.empty()
        for i, h in enumerate(holdings):
            sid = h["stock_id"]
            status.markdown(f"⏳ 分析 **{sid} {h.get('name', '')}** ({i + 1}/{len(holdings)})")
            r = _analyze_one_stock(sid)
            if r:
                scores[sid] = r
                update_holding_name(sid, r["company_name"])
            prog.progress((i + 1) / len(holdings))
        prog.empty(); status.empty()
        st.session_state[cache_key] = scores
        st.session_state[f"{cache_key}_time"] = datetime.datetime.now()

    scores = st.session_state.get(cache_key, {})
    if not scores:
        st.info(
            f"👆 **請按「🔄 重新評分」開始分析**（{len(holdings)} 檔約需 "
            f"{max(1, len(holdings) * 5 // 60)}–{len(holdings) * 8 // 60 + 1} 分鐘）。"
            "評分結果會保留，切換排名依據不會重跑。"
        )
        st.markdown("---")
        st.markdown("### 目前持股（尚未評分）")
        for h in holdings:
            st.markdown(f"- **{h['stock_id']} {h.get('name', '')}**　"
                        f"{float(h.get('shares') or 0):,.0f} 股 @ {float(h.get('cost') or 0):,.2f}")
        return

    # ── Build rows ────────────────────────────────────────────────────────────
    rows = []
    for h in holdings:
        sid = h["stock_id"]
        r = scores.get(sid)
        price = r["current_price"] if r else 0.0
        pos = compute_position(h, price)
        rows.append({"h": h, "r": r, "pos": pos})

    def _key_score(r):
        """The score used for ranking — long-horizon by default (evidence-backed)."""
        if not r:
            return -1
        if rank_by_long:
            # 連續趨勢分優先；沒有（舊快取）才退回離散長線分
            t = r.get("trend_score")
            if t is not None:
                return t
            return (r.get("horizon") or {}).get("long", {}).get("score",
                                                                r["total_score"])
        return r["total_score"]

    rows.sort(key=lambda x: _key_score(x["r"]), reverse=True)
    totals = portfolio_totals([x["pos"] for x in rows])

    # ── Portfolio summary ─────────────────────────────────────────────────────
    scored = [x for x in rows if x["r"]]
    avg_score = (sum(_key_score(x["r"]) for x in scored) / len(scored)) if scored else 0
    # Value-weighted score — what your money is actually exposed to
    mv_total = sum(x["pos"]["market_value"] for x in scored) or 1
    w_score = sum(_key_score(x["r"]) * x["pos"]["market_value"] for x in scored) / mv_total
    weak = [x for x in scored if _key_score(x["r"]) < 48]

    pnl_color = "#f03e3e" if totals["pnl"] >= 0 else "#2f9e44"
    s1, s2, s3, s4, s5 = st.columns(5)
    with s1:
        st.metric("持股檔數", totals["count"])
    with s2:
        st.metric("總成本", f"{totals['cost_value']:,.0f}")
    with s3:
        st.metric("總市值", f"{totals['market_value']:,.0f}")
    with s4:
        st.metric("未實現損益", f"{totals['pnl']:+,.0f}",
                  f"{totals['pnl_pct']:+.2f}%" if totals["pnl_pct"] is not None else None)
    with s5:
        score_name = "長線結構分" if rank_by_long else "綜合評分"
        st.metric(f"持股平均{score_name}", f"{avg_score:.0f}",
                  f"市值加權 {w_score:.0f}")

    # ── 用與選股頁相同的實證門檻判讀（避免兩頁標準不一致造成混亂）──────────────
    pf_thr = long_threshold()
    if rank_by_long:
        below = [x for x in scored if _key_score(x["r"]) < pf_thr]
        above = [x for x in scored if _key_score(x["r"]) >= pf_thr]
        mv_below = sum(x["pos"]["market_value"] for x in below)
        pct_below = mv_below / mv_total * 100 if mv_total else 0
        st.markdown(f"""
<div style="background:#161b26;border-left:4px solid {'#f44336' if pct_below > 50 else '#ff9800' if pct_below > 20 else '#4caf50'};
            border-radius:6px;padding:10px 14px;margin:6px 0;">
  <span style="font-weight:700;">🎯 實證門檻檢視（長線結構分 {pf_thr:.0f} 分）</span>
  <span style="color:#cfd8dc;font-size:13px;">
    —— {len(above)} 檔達標、<b>{len(below)} 檔未達標</b>，
    未達標部位佔總市值 <b>{pct_below:.0f}%</b>。
    回測顯示低於 {pf_thr:.0f} 分的區間，持有1個月的超額報酬全為負。
  </span>
</div>""", unsafe_allow_html=True)

        # ── 橋接：未達標的部位 → 目前選股頁有哪些達標標的可替換 ──────────────
        if below:
            cands = []
            for k in list(st.session_state.keys()):
                if k.startswith("smart_") and not k.endswith(("_scanned", "_time")) \
                        and isinstance(st.session_state[k], list):
                    cands = st.session_state[k]
                    break
            held = {x["h"]["stock_id"] for x in rows}
            alts = [c for c in cands
                    if c.get("stock_id") not in held
                    and (c.get("horizon") or {}).get("long", {}).get("score", 0) >= pf_thr
                    and (c.get("volume_adj", 0) or 0) >= 0]
            alts.sort(key=lambda c: (c.get("horizon") or {}).get("long", {}).get("score", 0),
                      reverse=True)
            with st.expander(
                    f"🔄 有 {len(below)} 檔未達標——看看選股頁目前有哪些達標標的？",
                    expanded=False):
                if alts:
                    st.markdown(
                        f"以下是**智能選股掃描結果中、你尚未持有、且長線分 ≥{pf_thr:.0f}** 的標的："
                    )
                    for c in alts[:8]:
                        lg = (c.get("horizon") or {}).get("long", {}).get("score", 0)
                        pe = c.get("pe_ratio")
                        st.markdown(
                            f"- **{c['stock_id']} {c['company_name']}**　長線分 **{lg}**"
                            f"　綜合 {c.get('total_score', '—')}"
                            + (f"　本益比 {pe:.1f}" if pe else "")
                        )
                    st.caption("⚠️ 這只是「分數比較」，不是換股建議。換股要考慮稅費、"
                               "你的持有成本與稅務狀況，且回測顯示頻繁交易會侵蝕報酬。")
                elif cands:
                    st.info("目前掃描結果中沒有『你未持有且達標』的標的。")
                else:
                    st.info("尚未執行選股掃描。請先到「🎯 智能選股」頁按『重新掃描』，"
                            "再回來這裡就會列出可比較的達標標的。")

    if weak:
        st.warning(
            f"⚠️ **需留意的部位**（{score_name} < 48）："
            + "、".join(f"{x['h']['stock_id']} {x['r']['company_name']}"
                       f"（{_key_score(x['r'])}分）" for x in weak)
            + " — 建議檢視是否減碼或設好停損。"
        )

    # ── Holdings ranked table ─────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(f"### 📊 持股排名（依{score_name}）")

    for rank, x in enumerate(rows, 1):
        h, r, pos = x["h"], x["r"], x["pos"]
        sid = h["stock_id"]
        name = (r["company_name"] if r else h.get("name", sid))
        rank_emoji = ["🥇", "🥈", "🥉"][rank - 1] if rank <= 3 else f"#{rank}"

        if r:
            color, icon, action = r["color"], r["icon"], r["action"]
            total = r["total_score"]
            p = r.get("potential") or {}
            pot = p.get("total", 0)
            rr = r.get("rr")
            stop_pct = r.get("stop_pct")
            price = r["current_price"]
            t_w, f_w, n_w = r["tech_score"], r["fund_score"], r["news_score"]
            hz = r.get("horizon") or {}
            # 共用元件（與選股頁同一份實作）：長線格在依長線分排名時加框
            hz_html = horizon_cells(hz, selected_key="long" if rank_by_long else None,
                                    show_legend=False, min_width=118)
            rr_txt = (f"風報比 <b style='color:#fafafa;'>{rr:.2f}</b>　停損 {abs(stop_pct):.1f}%"
                      if rr is not None and stop_pct is not None else "")
            pe_txt = pe_inline(r.get("pe_ratio"), r.get("dividend_yield"))
            # 實證區間徽章（共用元件）
            evid_html = evidence_badge((r.get("horizon_tech") or {}).get("long")
                                       or (hz.get("long") or {}).get("score"))
            oh_html = overheat_badge(r.get("overheat"))
        else:
            color, icon, action, total, pot = "#78909c", "❔", "無資料", 0, 0
            price, t_w, f_w, n_w = 0, 0, 0, 0
            hz_html, rr_txt, evid_html, pe_txt, oh_html = "", "", "", "", ""

        pl_color = "#f03e3e" if pos["pnl"] >= 0 else "#2f9e44"
        pnl_pct_txt = f"{pos['pnl_pct']:+.2f}%" if pos["pnl_pct"] is not None else "N/A"

        with st.container():
            st.markdown(f"""
<div class="stock-row" style="border:1px solid #2d3548;">
  <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
    <div style="min-width:44px;text-align:center;font-size:20px;">{rank_emoji}</div>
    <div style="min-width:150px;">
      <div style="font-size:17px;font-weight:900;">{sid}
        <span style="font-size:13px;font-weight:600;color:#ccc;">{name}</span></div>
      <div style="font-size:11px;color:#90a4ae;">
        {pos['shares']:,.0f} 股　成本 {pos['cost']:,.2f}</div>
      <div style="margin-top:3px;">{oh_html}</div>
    </div>
    <div style="min-width:96px;">
      <div style="font-size:11px;color:#90a4ae;">現價</div>
      <div style="font-size:16px;font-weight:700;">{price:,.2f}</div>
    </div>
    <div style="min-width:130px;">
      <div style="font-size:11px;color:#90a4ae;">未實現損益</div>
      <div style="font-size:16px;font-weight:800;color:{pl_color};">{pos['pnl']:+,.0f}</div>
      <div style="font-size:12px;color:{pl_color};">{pnl_pct_txt}</div>
    </div>
    <div style="min-width:92px;text-align:center;padding:4px 8px;
                box-shadow:0 0 0 2px {color};border-radius:10px;
                background:rgba(255,255,255,0.03);">
      <div style="font-size:26px;font-weight:900;color:{color};line-height:1;">{total}</div>
      <div style="font-size:10px;color:#aaa;margin-top:2px;">綜合評分</div>
    </div>
    <div style="min-width:74px;text-align:center;">
      <div style="font-size:14px;color:{color};font-weight:700;">{icon}</div>
      <div style="font-size:12px;color:{color};">{action}</div>
    </div>
    <div style="min-width:64px;text-align:center;">
      <div style="font-size:20px;font-weight:900;color:#7986cb;">{pot}</div>
      <div style="font-size:10px;color:#aaa;">潛力</div>
    </div>
    {hz_html}
    <div style="min-width:120px;font-size:11px;color:#aaa;">
      <div>技 <span style="color:#74c0fc;font-weight:700;">{t_w}</span>
           基 <span style="color:#a9e34b;font-weight:700;">{f_w}</span>
           息 <span style="color:#ffd43b;font-weight:700;">{n_w}</span></div>
      <div style="margin-top:3px;">{rr_txt}</div>
      <div style="margin-top:2px;">{pe_txt}</div>
    </div>
    {evid_html}
  </div>
</div>""", unsafe_allow_html=True)

            # 「為什麼選股頁沒看到這檔？」——用與選股頁完全相同的條件逐條檢核
            if r:
                cands = []
                for k in list(st.session_state.keys()):
                    if k.startswith("smart_") and not k.endswith(("_scanned", "_time")) \
                            and isinstance(st.session_state[k], list):
                        cands = st.session_state[k]
                        break
                if cands:
                    _bb = long_threshold()
                    _ctx = {"buy_bar": _bb, "horizon_key": None}
                    with st.expander(f"🔍 {sid} 在各選股策略中的入選情形", expanded=False):
                        st.caption("與智能選股頁**完全相同的條件**逐條檢核，"
                                   "所以這裡的結果一定和選股頁一致。")
                        for sd in STRATEGIES:
                            checks = strat_explain(sd, r, cands, _ctx)
                            passed = all(c["ok"] for c in checks)
                            head = f"{'✅ 入選' if passed else '❌ 未入選'}　**{sd['label']}**"
                            st.markdown(head)
                            for c in checks:
                                st.markdown(
                                    f"　　{'✅' if c['ok'] else '❌'} {c['detail']}")

            b1, b2, b3 = st.columns([1, 1, 4])
            with b1:
                if st.button("📊 詳細分析", key=f"pf_go_{sid}"):
                    st.session_state["stock_id"] = sid
                    st.session_state["_nav_to"] = "📊 個股分析"
                    st.rerun()
            with b2:
                if st.button("🗑 移除", key=f"pf_del_{sid}"):
                    remove_holding(sid)
                    st.session_state.pop(cache_key, None)
                    st.rerun()

    # ── Allocation chart ──────────────────────────────────────────────────────
    if len(rows) > 1 and totals["market_value"] > 0:
        st.markdown("---")
        st.markdown("### 🥧 持股配置與評分分佈")
        ac1, ac2 = st.columns(2)
        with ac1:
            fig_pie = go.Figure(go.Pie(
                labels=[f"{x['h']['stock_id']} {(x['r']['company_name'] if x['r'] else x['h'].get('name',''))}"
                        for x in rows],
                values=[x["pos"]["market_value"] for x in rows],
                hole=0.45, textinfo="label+percent", textfont=dict(size=11),
            ))
            fig_pie.update_layout(
                height=330, paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="#fafafa"), showlegend=False,
                margin=dict(l=10, r=10, t=30, b=10),
                title=dict(text="市值配置", font=dict(size=13, color="#aaa")),
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        with ac2:
            sc = [x for x in rows if x["r"]]
            fig_b = go.Figure(go.Bar(
                x=[f"{x['h']['stock_id']}<br>{x['r']['company_name']}" for x in sc],
                y=[_key_score(x["r"]) for x in sc],
                marker_color=[x["r"]["color"] for x in sc],
                text=[_key_score(x["r"]) for x in sc],
                textposition="outside", textfont=dict(color="#fafafa"),
            ))
            fig_b.add_hline(y=58, line_dash="dot", line_color="#a9e34b",
                            annotation_text="買進線 58", annotation_position="right")
            fig_b.add_hline(y=48, line_dash="dot", line_color="#ff9800",
                            annotation_text="觀望線 48", annotation_position="right")
            fig_b.update_layout(
                height=330, paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="#fafafa"), showlegend=False,
                yaxis=dict(range=[0, 105], showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
                margin=dict(l=10, r=90, t=30, b=10),
                title=dict(text=f"各持股{score_name}", font=dict(size=13, color="#aaa")),
            )
            st.plotly_chart(fig_b, use_container_width=True)

    st.markdown("---")
    st.caption(
        "💾 持股資料存於專案目錄的 `holdings.json`，可自行備份或編輯。"
        "⚠️ 評分為量化模型輸出，不構成投資建議。"
    )


# ─── Individual Stock Analysis ───────────────────────────────────────────────

# (period→rows now lives in services/analysis.PERIOD_ROWS, shared by both pages)


def render_stock_analysis(stock_id: str, period: str, as_of_date=None):
    today = datetime.date.today()
    is_backtest = as_of_date is not None and as_of_date < today

    # Shared preparation: always uses a long history (2y / 5y in backtest) so
    # MA120 / Vol_MA60 / the true 52-week range are all valid. The 分析期間
    # selector only slices the CHART, never the scoring basis.
    with st.spinner(f"載入 {stock_id} 資料中..."):
        try:
            df, df_full, intraday, has_intraday = prepare_frame(
                stock_id, as_of_date=as_of_date if is_backtest else None
            )
            info = get_ticker_info(stock_id)
            financials = get_financials(stock_id)
        except Exception as e:
            st.error(f"載入資料失敗: {e}")
            return

    if df is None or df.empty:
        st.error(f"找不到股票代碼 {stock_id} 的資料，請確認代碼是否正確。")
        return
    if is_backtest and len(df) < 30:
        st.error(f"基準日 {as_of_date} 之前的資料不足，無法分析，請選擇較近的日期。")
        return

    # Chart-only slice (scoring still uses the full `df`)
    df_display = df.tail(PERIOD_ROWS.get(period, 252))

    company_name = resolve_company_name(stock_id, info)
    if len(company_name) > 20:
        company_name = company_name[:15] + "..."
    if not is_backtest:
        update_watchlist_name(stock_id, company_name)

    if is_backtest:
        st.warning(
            f"🕐 **回測模式** — 以下所有分析（技術、量價、融資、目標價、買賣建議）"
            f"均還原至基準日 **{as_of_date}**（最後交易日 {df.index[-1].date()}）的狀態。"
            "基本面、新聞、分析師目標價因免費資料源限制仍為『目前』數值，僅技術面與價量籌碼為當日還原。"
        )

    current_price = df["Close"].iloc[-1]
    prev_price = df["Close"].iloc[-2] if len(df) > 1 else current_price
    chg = current_price - prev_price
    chg_pct = chg / prev_price * 100 if prev_price else 0
    high_52w = df["High"].rolling(min(252, len(df))).max().iloc[-1]
    low_52w = df["Low"].rolling(min(252, len(df))).min().iloc[-1]
    avg_vol = int(df["Volume"].rolling(20).mean().iloc[-1])

    if is_backtest:
        price_label = f"基準日收盤 ({df.index[-1].date()})"
    else:
        price_label = "現價 (近即時)" if has_intraday else "現價 (日線收盤)"

    col1, col2, col3, col4, col5, col6, col7 = st.columns([2, 1.4, 1.6, 1.4, 1.4, 1.6, 1])
    with col1:
        st.metric("公司", company_name)
    with col2:
        st.metric("股票代碼", stock_id)
    with col3:
        st.metric(price_label, f"{current_price:.2f}", f"{chg:+.2f} ({chg_pct:+.2f}%)")
    with col4:
        st.metric("52週高點", f"{high_52w:.2f}")
    with col5:
        st.metric("52週低點", f"{low_52w:.2f}")
    with col6:
        st.metric("20日均量", f"{avg_vol:,}")
    with col7:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if is_in_watchlist(stock_id):
            if st.button("★ 已自選", key="wl_toggle_main", use_container_width=True):
                remove_from_watchlist(stock_id)
                st.rerun()
        else:
            if st.button("☆ 加自選", key="wl_toggle_main", use_container_width=True):
                add_to_watchlist(stock_id, company_name)
                st.rerun()

    if is_backtest:
        st.caption(f"📅 回測基準日：{as_of_date}（顯示該日及以前的資料）")
    elif has_intraday:
        ts_str = intraday["timestamp"].strftime("%H:%M")
        st.caption(f"🔴 報價更新於 {ts_str}（每 60 秒重新整理，資料源 Yahoo Finance，非官方即時盤中報價）")
    else:
        st.caption("📅 目前顯示為最近一個交易日的日線收盤價（非盤中即時）")

    # Quick add/update this stock as a holding
    if not is_backtest:
        existing = get_holding(stock_id)
        exp_label = (f"💼 已持有 {existing['shares']:,.0f} 股 @ {existing['cost']:,.2f}"
                     f"（點此修改）" if existing else "💼 加入我的持股")
        with st.expander(exp_label, expanded=False):
            h1, h2, h3 = st.columns([1, 1, 0.6])
            with h1:
                q_shares = st.number_input(
                    "股數", min_value=0.0, step=1000.0,
                    value=float(existing.get("shares", 1000.0)) if existing else 1000.0,
                    key=f"hq_shares_{stock_id}")
            with h2:
                q_cost = st.number_input(
                    "成本價 (TWD)", min_value=0.0, step=1.0,
                    value=float(existing.get("cost", round(float(current_price), 2)))
                    if existing else round(float(current_price), 2),
                    key=f"hq_cost_{stock_id}")
            with h3:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                if st.button("儲存", key=f"hq_save_{stock_id}", use_container_width=True):
                    if q_shares > 0 and q_cost > 0:
                        upsert_holding(stock_id, company_name, q_shares, q_cost)
                        st.success(f"已存入持股：{q_shares:,.0f} 股 @ {q_cost:,.2f}")
                    else:
                        st.error("股數與成本價都必須大於 0。")
            if existing and st.button("🗑 從持股移除", key=f"hq_del_{stock_id}"):
                remove_holding(stock_id)
                st.rerun()

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 技術分析",
        "📰 新聞消息",
        "💹 基本面",
        "💰 目標價位",
        "🎯 投資建議",
    ])

    with tab1:
        render_technical_tab(df_display, stock_id, company_name)

    with tab2:
        render_news_tab(stock_id, company_name)

    with tab3:
        render_fundamental_tab(stock_id, info, financials)

    with tab4:
        fundamentals = analyze_fundamentals(info, financials)
        render_target_price_tab(df, info, fundamentals, company_name)

    with tab5:
        render_recommendation_tab(
            df, stock_id, info, company_name, financials,
            as_of_date=as_of_date if is_backtest else None,
            df_full=df_full if is_backtest else None,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Process navigation requests BEFORE any widgets are instantiated.
    # Widgets "own" their session_state key once rendered; setting it afterwards
    # raises StreamlitAPIException. Intercepting here (pre-render) is safe.
    nav = st.session_state.pop("_nav_to", None)
    if nav in ("📊 個股分析", "🎯 智能選股", "💼 我的持股"):
        st.session_state["main_page"] = nav

    stock_id, period, page, as_of_date = render_sidebar()

    st.title("📈 台灣股票分析系統")

    if page == "🎯 智能選股":
        render_smart_screener_page()
    elif page == "💼 我的持股":
        render_portfolio_page()
    else:
        render_stock_analysis(stock_id, period, as_of_date)


if __name__ == "__main__":
    main()
