import json
import os
import uuid

import streamlit as st

from graph import AUDIT_LOG_PATH, MOCK_CUSTOMERS, build_graph

st.set_page_config(page_title="Churn Risk HITL Review", layout="centered")

# Khởi tạo compiled graph trong session_state để không bị tạo lại mỗi lần rerun.
if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None

graph = st.session_state.graph

st.title("🏦 Churn Risk Agent — Human-in-the-Loop Review")

with st.sidebar:
    st.header("Đánh giá khách hàng mới")
    customer_id = st.selectbox("Customer ID", list(MOCK_CUSTOMERS.keys()))
    reviewer_id = st.text_input("Reviewer ID", value="operator_01")

    if st.button("🚀 Chạy đánh giá (evaluate_customer)"):
        thread_id = str(uuid.uuid4())
        st.session_state.thread_id = thread_id
        config = {"configurable": {"thread_id": thread_id}}
        graph.invoke(
            {
                "customer_id": customer_id,
                "proposed_action": "",
                "confidence_score": 0.0,
                "reasoning": "",
                "human_decision": None,
                "reviewer_id": reviewer_id,
            },
            config,
        )
        st.rerun()

if st.session_state.thread_id:
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    snapshot = graph.get_state(config)
    state = snapshot.values

    st.subheader(f"Customer ID: {state.get('customer_id', '-')}")
    col_a, col_b = st.columns(2)
    col_a.metric("Proposed Action", state.get("proposed_action", "-"))
    col_b.metric("Confidence", f"{state.get('confidence_score', 0):.2f}")
    st.write("**Reasoning:**")
    st.info(state.get("reasoning", "-"))

    pending_nodes = snapshot.next  # non-empty nếu graph đang bị interrupt

    if pending_nodes:
        st.warning(f"⏸️ Graph đang tạm dừng trước node: `{pending_nodes}` — cần human review.")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Approve", use_container_width=True):
                graph.update_state(config, {"human_decision": "approve"})
                graph.invoke(None, config)
                st.rerun()
        with col2:
            if st.button("❌ Reject", use_container_width=True):
                graph.update_state(config, {"human_decision": "reject"})
                graph.invoke(None, config)
                st.rerun()

        with st.expander("✏️ Edit action trước khi tiếp tục"):
            new_action = st.text_input(
                "Action đã chỉnh sửa", value=state.get("proposed_action", "")
            )
            if st.button("💾 Xác nhận Edit & Resume"):
                graph.update_state(
                    config,
                    {"proposed_action": new_action, "human_decision": "edit"},
                )
                graph.invoke(None, config)
                st.rerun()
    else:
        st.success("✅ Graph đã hoàn tất — action đã được xử lý và ghi vào audit log.")

st.divider()
st.header("📋 Audit Log")

if os.path.exists(AUDIT_LOG_PATH):
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
        try:
            audit_data = json.load(f)
        except json.JSONDecodeError:
            audit_data = []
else:
    audit_data = []

if audit_data:
    st.dataframe(list(reversed(audit_data)), use_container_width=True)
else:
    st.caption("Chưa có audit log entry nào.")
