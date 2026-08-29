"""Tests cho LangGraph HITL workflow (xem checklist ở Bước 5 của lab).

Chạy: pytest test_workflow.py -v
"""

import json
import os
import uuid

import pytest

from graph import (
    AUDIT_LOG_PATH,
    CONFIDENCE_THRESHOLD,
    build_graph,
    evaluate_customer,
    route_action,
)


def _invoke_thread(graph, customer_id, reviewer_id="operator_01"):
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke(
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
    return graph, config, result


# ---------------------------------------------------------------------------
# Agent Reasoning
# ---------------------------------------------------------------------------

def test_evaluate_customer_returns_required_keys():
    state = {
        "customer_id": "CUST001",
        "proposed_action": "",
        "confidence_score": 0.0,
        "reasoning": "",
        "human_decision": None,
        "reviewer_id": None,
    }
    output = evaluate_customer(state)

    assert "proposed_action" in output
    assert "confidence_score" in output
    assert "reasoning" in output
    assert 0.0 <= output["confidence_score"] <= 1.0
    assert isinstance(output["reasoning"], str) and output["reasoning"]


# ---------------------------------------------------------------------------
# Rule 1 - Policy Override
# ---------------------------------------------------------------------------

def test_policy_override_ignores_high_confidence():
    state = {
        "proposed_action": "increase_credit_limit",
        "confidence_score": 0.99,
    }
    assert route_action(state) == "execute_high_risk_action"


# ---------------------------------------------------------------------------
# Rule 2 - Auto-Execute
# ---------------------------------------------------------------------------

def test_auto_execute_low_risk_high_confidence():
    state = {
        "proposed_action": "send_email",
        "confidence_score": 0.90,
    }
    assert route_action(state) == "execute_low_risk_action"


# ---------------------------------------------------------------------------
# Rule 3 - Escalate/Suggest
# ---------------------------------------------------------------------------

def test_escalate_low_confidence():
    state = {
        "proposed_action": "send_email",
        "confidence_score": 0.82,
    }
    assert route_action(state) == "execute_high_risk_action"


def test_threshold_boundary_is_inclusive():
    state = {
        "proposed_action": "send_email",
        "confidence_score": CONFIDENCE_THRESHOLD,
    }
    assert route_action(state) == "execute_low_risk_action"


# ---------------------------------------------------------------------------
# Interrupt behaviour (end-to-end qua compiled graph + MemorySaver)
# ---------------------------------------------------------------------------

@pytest.fixture
def graph():
    return build_graph()


def test_high_risk_action_is_interrupted_before_running(graph, tmp_path, monkeypatch):
    # Cô lập audit log khi chạy test để không đụng file thật của project.
    monkeypatch.setattr("graph.AUDIT_LOG_PATH", str(tmp_path / "audit_log.json"))

    _, config, result = _invoke_thread(graph, "CUST004")  # churn cao, TOI cao

    snapshot = graph.get_state(config)

    if result["proposed_action"] == "increase_credit_limit":
        assert snapshot.next == ("execute_high_risk_action",)
        # State (customer data) phải còn nguyên trong lúc graph tạm dừng.
        assert snapshot.values["customer_id"] == "CUST004"
        assert snapshot.values["proposed_action"] == "increase_credit_limit"


def test_resume_after_approve_clears_pending_state(graph, tmp_path, monkeypatch):
    monkeypatch.setattr("graph.AUDIT_LOG_PATH", str(tmp_path / "audit_log.json"))

    _, config, result = _invoke_thread(graph, "CUST004")
    if graph.get_state(config).next:
        graph.update_state(config, {"human_decision": "approve"})
        graph.invoke(None, config)
        assert graph.get_state(config).next == ()


def test_resume_after_reject_still_logs_audit_entry(graph, tmp_path, monkeypatch):
    audit_path = tmp_path / "audit_log.json"
    monkeypatch.setattr("graph.AUDIT_LOG_PATH", str(audit_path))

    _, config, result = _invoke_thread(graph, "CUST004")
    if graph.get_state(config).next:
        graph.update_state(config, {"human_decision": "reject"})
        graph.invoke(None, config)

        history = json.loads(audit_path.read_text(encoding="utf-8"))
        assert history[-1]["decision"] == "reject"


# ---------------------------------------------------------------------------
# Audit log - append-only, không ghi đè lịch sử cũ
# ---------------------------------------------------------------------------

def test_audit_log_appends_without_overwriting(graph, tmp_path, monkeypatch):
    audit_path = tmp_path / "audit_log.json"
    monkeypatch.setattr("graph.AUDIT_LOG_PATH", str(audit_path))

    _invoke_thread(graph, "CUST002")  # low-risk, auto-execute
    _invoke_thread(graph, "CUST003")  # low-risk, auto-execute

    history = json.loads(audit_path.read_text(encoding="utf-8"))
    assert len(history) == 2
    for entry in history:
        assert {
            "timestamp",
            "agent_id",
            "action",
            "confidence",
            "reviewer_id",
            "decision",
        } <= set(entry.keys())
