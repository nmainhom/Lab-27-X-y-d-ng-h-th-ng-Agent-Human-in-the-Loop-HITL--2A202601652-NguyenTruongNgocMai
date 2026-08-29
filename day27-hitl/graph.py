from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from models import AuditEntry

AGENT_ID = "churn-risk-agent"

# Rule 2/3 threshold: confidence_score >= threshold -> auto-execute (nếu low-risk),
# ngược lại luôn bị escalate để human review.
CONFIDENCE_THRESHOLD = 0.85

# Rule 1 - Hard policy rule: các action nằm trong tập này LUÔN phải qua human
# review, bất kể confidence_score cao đến đâu.
HIGH_RISK_POLICY_ACTIONS = {"increase_credit_limit"}

AUDIT_LOG_PATH = os.path.join(os.path.dirname(__file__), "audit_log.json")


class GraphState(TypedDict):
    customer_id: str
    proposed_action: str
    confidence_score: float
    reasoning: str
    human_decision: str | None
    reviewer_id: str | None  # ai sẽ review nếu bị escalate


# Mock "database" khách hàng: churn probability + Total Operating Income (TOI).
# Trong thực tế đây sẽ là một feature store / data warehouse query.
MOCK_CUSTOMERS: dict[str, dict] = {
    "CUST001": {"churn_probability": 0.82, "toi": 15_000_000},
    "CUST002": {"churn_probability": 0.35, "toi": 3_000_000},
    "CUST003": {"churn_probability": 0.55, "toi": 8_000_000},
    "CUST004": {"churn_probability": 0.91, "toi": 25_000_000},
    "CUST005": {"churn_probability": 0.78, "toi": 4_000_000},
}


def _get_customer_profile(customer_id: str) -> dict:
    return MOCK_CUSTOMERS.get(customer_id, {"churn_probability": 0.5, "toi": 5_000_000})


def evaluate_customer(state: GraphState) -> dict:
    """Agent Reasoning node.

    Giả lập một agent đánh giá TOI và churn probability của khách hàng, rồi đề
    xuất một action kèm confidence_score (0.0 -> 1.0) và reasoning.
    """
    profile = _get_customer_profile(state["customer_id"])
    churn = profile["churn_probability"]
    toi = profile["toi"]

    if churn >= 0.7 and toi >= 10_000_000:
        proposed_action = "increase_credit_limit"
        confidence = round(min(0.99, 0.70 + churn * 0.25), 2)
        reasoning = (
            f"Customer has high churn probability ({churn:.0%}) and high TOI "
            f"({toi:,} VND). Increasing the credit limit is likely to improve "
            "retention."
        )
    else:
        proposed_action = "send_email"
        confidence = round(min(0.99, 0.55 + (1 - abs(churn - 0.5)) * 0.4), 2)
        reasoning = (
            f"Customer has churn probability {churn:.0%} with TOI {toi:,} VND. "
            "No high-risk indicator found; a retention email is a sufficient "
            "low-risk action."
        )

    return {
        "proposed_action": proposed_action,
        "confidence_score": confidence,
        "reasoning": reasoning,
    }


def route_action(state: GraphState) -> str:
    """Conditional edge function: Confidence Routing + Hard Rules.

    Thực hiện đúng 3 rule theo thứ tự ưu tiên:
      1. Policy Override  - action nhạy cảm luôn phải qua human review.
      2. Auto-Execute     - confidence đủ cao và không bị hard rule chặn.
      3. Escalate/Suggest - confidence thấp -> ép buộc human review.
    """
    action = state["proposed_action"]
    confidence = state["confidence_score"]

    # Rule 1 - Policy Override: confidence_score cao không được phép bypass policy.
    if action in HIGH_RISK_POLICY_ACTIONS:
        return "execute_high_risk_action"

    # Rule 2 - Auto-Execute.
    if confidence >= CONFIDENCE_THRESHOLD:
        return "execute_low_risk_action"

    # Rule 3 - Escalate/Suggest.
    return "execute_high_risk_action"


def _read_audit_log() -> list[dict]:
    if not os.path.exists(AUDIT_LOG_PATH):
        return []
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _append_audit_log(entry: AuditEntry) -> None:
    # Đọc lịch sử hiện có -> append -> ghi lại toàn bộ danh sách, KHÔNG overwrite
    # bằng một object đơn lẻ.
    history = _read_audit_log()
    history.append(entry.model_dump())
    with open(AUDIT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def execute_low_risk_action(state: GraphState) -> dict:
    """Auto-Execute path: action low-risk với confidence đủ cao."""
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        agent_id=AGENT_ID,
        action=state["proposed_action"],
        confidence=state["confidence_score"],
        reviewer_id="auto-system",
        decision="auto_execute",
    )
    _append_audit_log(entry)
    print(
        f"[AUTO-EXECUTE] {state['customer_id']}: '{state['proposed_action']}' "
        f"(confidence={state['confidence_score']})"
    )
    return {}


def execute_high_risk_action(state: GraphState) -> dict:
    """Node bị interrupt_before.

    Graph dừng TRƯỚC khi node này chạy. Khi được resume (sau khi Streamlit đã
    gọi update_state với human_decision), node kiểm tra quyết định của con
    người rồi mới thực thi / hủy action.
    """
    decision = state.get("human_decision") or "no_decision"
    reviewer_id = state.get("reviewer_id") or "unknown_reviewer"
    action = state["proposed_action"]

    if decision == "approve":
        print(f"[EXECUTE] {state['customer_id']}: approved -> executing '{action}'")
    elif decision == "reject":
        print(f"[ABORT] {state['customer_id']}: rejected -> action '{action}' NOT executed")
    elif decision == "edit":
        print(f"[EXECUTE-EDITED] {state['customer_id']}: executing edited action '{action}'")
    else:
        print(f"[WARN] {state['customer_id']}: no valid human_decision, defaulting to abort")

    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        agent_id=AGENT_ID,
        action=action,
        confidence=state["confidence_score"],
        reviewer_id=reviewer_id,
        decision=decision,
    )
    _append_audit_log(entry)
    return {}


def build_graph():
    """Build và compile LangGraph workflow với MemorySaver + interrupt_before."""
    builder = StateGraph(GraphState)

    builder.add_node("evaluate_customer", evaluate_customer)
    builder.add_node("execute_low_risk_action", execute_low_risk_action)
    builder.add_node("execute_high_risk_action", execute_high_risk_action)

    builder.add_edge(START, "evaluate_customer")
    builder.add_conditional_edges(
        "evaluate_customer",
        route_action,
        {
            "execute_low_risk_action": "execute_low_risk_action",
            "execute_high_risk_action": "execute_high_risk_action",
        },
    )
    builder.add_edge("execute_low_risk_action", END)
    builder.add_edge("execute_high_risk_action", END)

    memory = MemorySaver()
    graph = builder.compile(
        checkpointer=memory,
        interrupt_before=["execute_high_risk_action"],
    )
    return graph
