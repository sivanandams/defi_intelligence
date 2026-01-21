# ============================================================
# DeFi Intelligence Terminal — FINAL v3
# ============================================================

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="DeFi Intelligence Terminal",
    page_icon="📊",
    layout="wide"
)

# ============================================================
# API ENDPOINTS
# ============================================================
FEES_API = "https://api.llama.fi/overview/fees?excludeChain=true"
DEXS_API = "https://api.llama.fi/overview/dexs?excludeChain=true"
YIELDS_API = "https://yields.llama.fi/pools"

# ============================================================
# HELPERS
# ============================================================
@st.cache_data(ttl=3600)
def fetch(url):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def safe_column(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


# ============================================================
# DATA LOADERS (SCHEMA SAFE)
# ============================================================
def load_fees():
    d = fetch(FEES_API)
    if not d:
        return pd.DataFrame()

    df = pd.DataFrame(d.get("protocols", []))
    required = {"name", "category", "total24h", "change_7d"}
    if not required.issubset(df.columns):
        return pd.DataFrame()

    return df[list(required)].sort_values("change_7d", ascending=False)


def load_dexs():
    d = fetch(DEXS_API)
    if not d:
        return pd.DataFrame()

    df = pd.DataFrame(d.get("protocols", []))
    if df.empty:
        return pd.DataFrame()

    user_col = safe_column(df, ["dailyUsers", "users", "activeUsers"])

    out = df[["name", "category"]].copy()
    if user_col:
        out["users"] = df[user_col]

    return out


def load_yields():
    d = fetch(YIELDS_API)
    if not d:
        return pd.DataFrame()

    df = pd.DataFrame(d.get("data", []))
    if df.empty:
        return pd.DataFrame()

    for col in ["apy", "tvlUsd"]:
        if col not in df.columns:
            return pd.DataFrame()

    if "category" not in df.columns:
        df["category"] = "Yield"

    return df[df["apy"] > 8][
        ["project", "chain", "category", "apy", "tvlUsd"]
    ]


# ============================================================
# INTELLIGENCE LAYERS
# ============================================================
def compute_trend_score(row):
    score = 0
    score += min(row.get("change_7d", 0), 50) * 0.6
    score += min(row.get("total24h", 0) / 1e7, 20)
    return round(min(score, 100), 2)


def whale_flow_signal(row):
    tvl = row.get("TVL (USD)", 0)
    apy = row.get("APY (%)", 0)

    if tvl > 500_000_000 and apy < 8:
        return "🐳 Accumulation"
    if tvl > 500_000_000 and apy > 20:
        return "🐳 Distribution"
    if tvl < 50_000_000 and apy > 25:
        return "🐟 Retail Farming"
    return "Neutral"


def detect_narratives(fees_df, dex_df, yield_df):
    narratives = {}

    for df, signal in [
        (fees_df, "fees"),
        (dex_df, "users"),
        (yield_df, "liquidity"),
    ]:
        if df.empty or "category" not in df.columns:
            continue

        for cat in df["category"].dropna().unique():
            narratives.setdefault(cat, set()).add(signal)

    rows = []
    for cat, signals in narratives.items():
        strength = len(signals)
        status = (
            "🔥 Accelerating" if strength >= 3 else
            "🟢 Emerging" if strength == 2 else
            "🧊 Mature"
        )
        rows.append({
            "Narrative": cat,
            "Signals Active": ", ".join(sorted(signals)),
            "Strength": strength,
            "Status": status
        })

    return pd.DataFrame(rows).sort_values("Strength", ascending=False)


# ============================================================
# EMAIL (OPTIONAL)
# ============================================================
def send_email(subject, body):
    server = os.getenv("SMTP_SERVER")
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASSWORD")
    port = int(os.getenv("SMTP_PORT", "587"))

    if not all([server, user, pwd]):
        return False

    msg = MIMEText(body)
    msg["From"] = user
    msg["To"] = user
    msg["Subject"] = subject

    with smtplib.SMTP(server, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)

    return True


# ============================================================
# LOAD DATA
# ============================================================
fees_df = load_fees()
dex_df = load_dexs()
yield_df = load_yields()

# ---- Fees ----
if not fees_df.empty:
    fees_df["Trend Score (0–100)"] = fees_df.apply(compute_trend_score, axis=1)
    fees_df.rename(columns={
        "change_7d": "7D Fee Change (%)",
        "total24h": "24H Fees (USD)"
    }, inplace=True)

# ---- Yields ----
if not yield_df.empty:
    yield_df.rename(columns={
        "project": "Protocol",
        "apy": "APY (%)",
        "tvlUsd": "TVL (USD)"
    }, inplace=True)

    yield_df["Whale Signal"] = yield_df.apply(whale_flow_signal, axis=1)

narratives_df = detect_narratives(fees_df, dex_df, yield_df)

# ============================================================
# UI
# ============================================================
st.title("📊 DeFi Intelligence Terminal")
st.caption(f"Last refresh: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

if st.button("📧 Send Daily Brief") and not fees_df.empty:
    body = "\n".join(
        f"{r['name']} ({r['category']}): {r['7D Fee Change (%)']:.2f}%"
        for _, r in fees_df.head(5).iterrows()
    )
    if send_email("DeFi Daily Brief", body):
        st.success("Email sent ✅")
    else:
        st.warning("Email not configured")

# ============================================================
# LAYOUT
# ============================================================
left, right = st.columns([1.2, 2])

with left:
    st.subheader("🔥 Fee Leaders (7D)")
    st.dataframe(
        fees_df[[
            "name",
            "category",
            "7D Fee Change (%)",
            "Trend Score (0–100)"
        ]].head(15),
        use_container_width=True,
        hide_index=True
    )

with right:
    if not fees_df.empty:
        fig = px.bar(
            fees_df.head(10),
            x="name",
            y="7D Fee Change (%)",
            color="category",
            title="Top Fee Growth (%)"
        )
        st.plotly_chart(fig, use_container_width=True)

# ============================================================
# NARRATIVES
# ============================================================
st.subheader("🧠 Narrative Detection")
if not narratives_df.empty:
    st.dataframe(narratives_df, use_container_width=True)

# ============================================================
# YIELD + WHALES
# ============================================================
st.subheader("💧 Yield & Whale Signals")
if not yield_df.empty:
    st.dataframe(
        yield_df.sort_values("APY (%)", ascending=False),
        use_container_width=True
    )

# ============================================================
# LOCAL LLM CHAT (OLLAMA)
# ============================================================
IS_CLOUD = os.getenv("STREAMLIT_SERVER_PORT") is not None

if not IS_CLOUD:
    st.subheader("🤖 Local LLM Analyst (Ollama)")
else:
    st.info("Local LLM disabled on Streamlit Cloud")

query = st.text_input("Ask a question about today's data")

if query:
    try:
        import ollama

        context = fees_df.head(20).to_csv(index=False)
        prompt = f"""
You are a DeFi analyst.
Answer ONLY using the data below.

DATA:
{context}

QUESTION:
{query}
"""

        response = ollama.chat(
            model="llama3",
            messages=[{"role": "user", "content": prompt}]
        )

        st.markdown("### 🧠 Answer")
        st.write(response["message"]["content"])

    except Exception:
        st.error("Local LLM not available. Ensure Ollama is running.")