#!/usr/bin/env python3
"""dashboard/app.py — v0.1

Claude Code OS Dashboard：localhost-only、read-only 系統狀態檢視。
不提供任何修改/刪除/重跑 job 的操作——這是刻意的範圍限制，不是漏做。
資料存取全部透過 dashboard/data.py，不 import hermes/db.py。

啟動（Windows）：
    .venv/Scripts/python.exe -m streamlit run dashboard/app.py --server.address=localhost
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

st.set_page_config(page_title="Claude Code OS Dashboard", layout="wide")

st.title("Claude Code OS — Dashboard")
st.caption("localhost-only・read-only・手動啟動，不是常駐服務")

if st.button("🔄 重新整理"):
    st.cache_data.clear()
    st.rerun()

# ClaudeCodeOSWin 複本：jobs.db 是 runtime 資料（gitignored），環境搬遷後
# 要等 worker/adapter 第一次執行 init_db() 才會出現。缺檔時不噴 exception，
# 明確告知狀態，Jobs／成本分頁顯示空資料。
if not data.jobs_db_exists():
    st.warning(
        "hermes/jobs.db 尚未建立（runtime 資料，不在 git 裡）。"
        "worker 或 adapter 第一次執行後就會建立；在那之前 Jobs／成本分頁會是空的。"
    )

# ClaudeCodeOSWin 複本：常駐機制從 launchd 換成 systemd user service，
# label 對應也從 launchd 的 reverse-DNS label 換成 systemd unit 檔名。
SYSTEMD_LABELS = {
    "hermes-worker.service": "Worker",
    "hermes-telegram.service": "Telegram Adapter",
    "hermes-cron-daily-memory-check.timer": "Cron（daily-memory-check）",
    "hermes-rss.timer": "RSS Adapter",
}


@st.cache_data(ttl=5)
def _status_counts():
    return data.get_status_counts()


@st.cache_data(ttl=5)
def _launchd_status():
    return data.get_systemd_status()


@st.cache_data(ttl=5)
def _domain_status():
    return data.get_domain_status()


@st.cache_data(ttl=5)
def _adapter_config_status():
    return data.get_adapter_config_status()


@st.cache_data(ttl=5)
def _recent_jobs(limit, status, source):
    return data.get_recent_jobs(limit=limit, status=status, source=source)


@st.cache_data(ttl=5)
def _cost_summary():
    return data.get_cost_summary()


@st.cache_data(ttl=5)
def _memory_inbox_counts():
    return data.get_memory_inbox_counts()


@st.cache_data(ttl=5)
def _memory_files():
    return data.get_memory_files()


tab_overview, tab_jobs, tab_cost, tab_memory, tab_logs = st.tabs(
    ["總覽", "Jobs", "成本", "Memory", "Logs"]
)

with tab_overview:
    st.subheader("Worker / Adapter 狀態")
    st.caption("ClaudeCodeOSWin 複本：狀態來源是 `systemctl --user`，不是 macOS 版本用的 `launchctl`。")
    systemd_status = _launchd_status()
    cols = st.columns(len(SYSTEMD_LABELS))
    for col, (unit_name, display_name) in zip(cols, SYSTEMD_LABELS.items()):
        info = systemd_status.get(unit_name)
        with col:
            if info is None:
                st.metric(display_name, "未安裝")
            else:
                running = "active" in info["last_exit"].split("/")[0]
                st.metric(display_name, "運作中" if running else "已停止（等排程）", info["last_exit"])

    st.subheader("Adapter 設定狀態")
    st.caption("只顯示「有沒有設定、設定了幾筆」，不會顯示 bot token 等密鑰本身。")
    st.json(_adapter_config_status())

    st.subheader("Job 狀態統計")
    counts = _status_counts()
    cols = st.columns(len(data.JOB_STATUSES))
    for col, status in zip(cols, data.JOB_STATUSES):
        col.metric(status, counts.get(status, 0))

    st.subheader("領域狀態")
    domains = _domain_status()
    if domains:
        st.dataframe(pd.DataFrame(domains), width="stretch", hide_index=True)
    else:
        st.info("找不到 registry/agents.yaml")

with tab_jobs:
    st.subheader("最近 Jobs")
    col1, col2, col3 = st.columns(3)
    with col1:
        limit = st.number_input("顯示筆數", min_value=5, max_value=500, value=50, step=5)
    with col2:
        status_filter = st.selectbox("篩選狀態", ["（全部）"] + data.JOB_STATUSES)
    with col3:
        source_filter = st.selectbox("篩選來源", ["（全部）", "telegram", "cron", "rss", "manual", "sat"])

    jobs = _recent_jobs(
        int(limit),
        None if status_filter == "（全部）" else status_filter,
        None if source_filter == "（全部）" else source_filter,
    )
    if jobs:
        df = pd.DataFrame(jobs)[
            ["id", "status", "source", "thread_id", "attempts", "max_attempts",
             "cost_usd", "created_at", "completed_at"]
        ]
        st.dataframe(df, width="stretch", height=300, hide_index=True)
    else:
        st.info("沒有符合條件的 job")

    st.subheader("單筆 Job 詳細內容")
    job_id_input = st.text_input("貼上 job id")
    if job_id_input:
        job = data.get_job(job_id_input.strip())
        if job is None:
            st.error("找不到這個 job id")
        else:
            st.json(job)
            st.subheader("對應的 log")
            st.code(data.tail_log(f"{job_id_input.strip()}.log", lines=200), language=None)

with tab_cost:
    st.subheader("成本統計")
    cost = _cost_summary()
    col1, col2, col3 = st.columns(3)
    col1.metric("總成本 (USD)", f"${cost['total']:.4f}")
    col2.metric("平均成本 (USD)", f"${cost['avg']:.4f}")
    col3.metric("計費 job 數", cost["count"])

    if cost["by_source"]:
        df = pd.DataFrame(cost["by_source"])
        st.bar_chart(df.set_index("source")["total"])
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("目前沒有任何有成本紀錄的 job")

with tab_memory:
    st.subheader("Memory Inbox")
    inbox_counts = _memory_inbox_counts()
    col1, col2, col3 = st.columns(3)
    col1.metric("待整併 (pending)", inbox_counts["pending"])
    col2.metric("已整併 (processed)", inbox_counts["processed"])
    col3.metric("已拒收 (failed)", inbox_counts["failed"])

    st.subheader("正本記憶檔案")
    files = _memory_files()
    if files:
        for f in files:
            st.text(f)
    else:
        st.info("memory/ 底下沒有正本檔案")

with tab_logs:
    st.subheader("Logs")
    log_choice = st.selectbox(
        "選擇 log 檔案",
        ["worker.log", "telegram_adapter.log", "cron_adapter.log", "rss_adapter.log"],
    )
    num_lines = st.slider("顯示行數", min_value=20, max_value=1000, value=200, step=20)
    st.code(data.tail_log(log_choice, lines=num_lines), language=None)
