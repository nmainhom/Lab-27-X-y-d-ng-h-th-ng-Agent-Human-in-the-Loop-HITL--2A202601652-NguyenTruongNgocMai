# Day 27 — Churn Risk Agent with Human-in-the-Loop (HITL)

LangGraph workflow đánh giá rủi ro khách hàng rời bỏ (churn risk), kết hợp
confidence routing, hard policy rules và cơ chế Human-in-the-Loop qua Streamlit.

## 1. Cài dependency

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Yêu cầu Python 3.10+.

## 2. Cách chạy LangGraph workflow

Workflow được định nghĩa trong [graph.py](graph.py) và được import + chạy trực
tiếp từ Streamlit UI (`build_graph()`). Có thể test nhanh workflow độc lập:

```bash
python -c "
import uuid
from graph import build_graph

graph = build_graph()
config = {'configurable': {'thread_id': str(uuid.uuid4())}}
result = graph.invoke({
    'customer_id': 'CUST001',
    'proposed_action': '',
    'confidence_score': 0.0,
    'reasoning': '',
    'human_decision': None,
    'reviewer_id': 'operator_01',
}, config)
print(result)
print('next:', graph.get_state(config).next)
"
```

Nếu `next` không rỗng, nghĩa là graph đang bị `interrupt_before` trước
`execute_high_risk_action`, chờ human review.

## 3. Cách chạy Streamlit UI

```bash
streamlit run app.py
```

Trong sidebar: chọn `Customer ID` + `Reviewer ID`, bấm **Chạy đánh giá** để agent
(`evaluate_customer`) đưa ra `proposed_action`, `confidence_score`, `reasoning`.

## 4. Confidence threshold đang sử dụng

```
CONFIDENCE_THRESHOLD = 0.85
```

(khai báo trong [graph.py](graph.py))

## 5. Hard policy rule

Nếu `proposed_action == "increase_credit_limit"`, workflow **luôn** route tới
`execute_high_risk_action` để chờ human review — bất kể `confidence_score` cao
đến đâu (kể cả 0.99). Confidence cao KHÔNG được phép bypass policy này (Rule 1
- Policy Override trong `route_action`).

Routing tổng quát (`route_action` trong graph.py):

1. **Policy Override** — `increase_credit_limit` → luôn `execute_high_risk_action`.
2. **Auto-Execute** — action khác + `confidence_score >= 0.85` → `execute_low_risk_action` (tự động thực hiện, không cần human).
3. **Escalate/Suggest** — action khác + `confidence_score < 0.85` → `execute_high_risk_action` (ép buộc human review).

## 6. Cách Approve, Reject và Edit

Khi graph bị interrupt (đang chờ ở `execute_high_risk_action`), Streamlit UI
hiển thị 3 lựa chọn:

- **Approve** → `graph.update_state(config, {"human_decision": "approve"})` rồi
  `graph.invoke(None, config)` để resume; node thực thi action.
- **Reject** → tương tự với `human_decision = "reject"`; node **không** thực
  hiện action (abort).
- **Edit** → nhập lại `proposed_action` mới, sau đó
  `graph.update_state(config, {"proposed_action": new_action, "human_decision": "edit"})`
  rồi resume; node thực hiện action đã được chỉnh sửa.

Trong mọi trường hợp, `execute_high_risk_action` ghi một `AuditEntry` mới vào
audit log trước khi kết thúc.

## 7. Audit log được lưu ở đâu

File JSON cục bộ: [audit_log.json](audit_log.json), dạng danh sách các
`AuditEntry` (Pydantic model, xem [models.py](models.py)):

```json
[
  {
    "timestamp": "2026-08-29T09:00:00+00:00",
    "agent_id": "churn-risk-agent",
    "action": "increase_credit_limit",
    "confidence": 0.94,
    "reviewer_id": "operator_01",
    "decision": "approve"
  }
]
```

Mỗi lần ghi log, hệ thống đọc lại toàn bộ lịch sử hiện có → append entry mới →
ghi lại danh sách, để không bao giờ overwrite lịch sử cũ. Trong production nên
chuyển sang một append-only database (vd. PostgreSQL) để tăng độ tin cậy và
khả năng kiểm toán.

## 8. Chạy test

```bash
pytest test_workflow.py -v
```

[test_workflow.py](test_workflow.py) kiểm tra: `evaluate_customer` trả đủ key
+ confidence trong `[0.0, 1.0]`, cả 3 rule của `route_action` (Policy Override,
Auto-Execute, Escalate), graph thực sự dừng ở `interrupt_before` và giữ
nguyên state, resume sau Approve/Reject, và audit log append-only.

## Cấu trúc project

```
day27-hitl/
├── .streamlit/
│   └── config.toml     # Cấu hình Streamlit (theme, server) — không chứa secret
├── .gitignore           # Loại trừ venv, __pycache__, .env, secrets.toml
├── app.py                # Streamlit UI, human approval logic, resume graph logic
├── graph.py              # GraphState, agent nodes, routing, graph compilation
├── models.py              # AuditEntry (Pydantic)
├── audit_log.json         # Audit trail (append-only JSON list)
├── test_workflow.py       # pytest: routing rules, interrupt, audit log
├── reflection_answers.md  # Trả lời 3 Reflection Questions của lab
├── README.md
└── requirements.txt
```

## Checklist tự kiểm tra (theo Bước 5 của lab)

- [x] `GraphState` có đủ `customer_id`, `proposed_action`, `confidence_score`,
      `reasoning`, `human_decision`.
- [x] State tồn tại xuyên suốt graph, không mất khi bị interrupt (nhờ
      `MemorySaver` + `checkpointer=memory`).
- [x] `human_decision` cập nhật được từ Streamlit qua `graph.update_state`.
- [x] `route_action` trả về confidence trong khoảng `0.0 -> 1.0`.
- [x] `increase_credit_limit` với confidence 0.99 vẫn phải qua Human Review
      (không auto-execute).
- [x] `send_email` với confidence 0.90 → `execute_low_risk_action`.
- [x] `send_email` với confidence 0.82 → Human Review (`execute_high_risk_action`).
- [x] Graph compile với `checkpointer=memory` và
      `interrupt_before=["execute_high_risk_action"]`.
- [x] Streamlit hiển thị đủ `proposed_action`, `confidence_score`, `reasoning`,
      nút Approve/Reject/Edit.
- [x] Approve/Reject/Edit đều được ghi vào `audit_log.json`, không ghi đè lịch
      sử cũ.
