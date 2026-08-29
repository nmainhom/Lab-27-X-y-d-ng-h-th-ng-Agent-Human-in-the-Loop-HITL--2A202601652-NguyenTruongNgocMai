# Reflection Answers — Lab 27 (Churn Risk HITL)

## Câu 1 — `interrupt_before` hay `interrupt_after`?

Nếu mục tiêu là để con người **rewrite nội dung** mà một node vừa generate
(customer retention email) **trước khi** nó di chuyển tới routing node, nên
dùng:

```python
interrupt_after=["generate_email"]
```

**Tại sao không phải `interrupt_before`:**

- `interrupt_before=["some_node"]` tạm dừng graph **trước khi** một node cụ
  thể chạy. Muốn dùng cách này, ta phải trỏ đúng vào node kế tiếp trong đồ thị
  (routing node) — nếu sau này thêm một node trung gian giữa `generate_email`
  và routing node, điểm dừng sẽ không còn đúng vị trí mong muốn nữa (bug ẩn).
- `interrupt_after=["generate_email"]` gắn trực tiếp vào node sinh ra nội
  dung. Ngay khi node đó chạy xong, state đã chứa email vừa generate và graph
  dừng lại ngay tại đó — đảm bảo con người luôn thấy đúng nội dung mới nhất để
  edit, bất kể cấu trúc graph phía sau thay đổi thế nào.
- Về mặt ngữ nghĩa: `interrupt_before` phù hợp khi mục tiêu là **ngăn một
  hành động xảy ra** (như `execute_high_risk_action` trong lab — action chưa
  từng chạy). `interrupt_after` phù hợp khi mục tiêu là **can thiệp vào output
  vừa được tạo ra** trước khi nó được dùng tiếp.

## Câu 2 — Chống Alert Fatigue

Tình huống: 500 `send_email`/ngày bị kẹt ở confidence 0.82, ngay dưới
threshold 0.85 → tất cả đều bị escalate cho con người, gây quá tải.

Thay đổi cụ thể:

1. **Threshold theo action type, không dùng một threshold toàn cục.**
   `send_email` là hành động ít rủi ro, có thể revert (gửi email sai chỉ cần
   gửi email đính chính) — threshold cho action này nên thấp hơn nhiều so với
   `increase_credit_limit` (vd. 0.70 thay vì 0.85). Hard policy rule (Rule 1)
   vẫn giữ nguyên cho action nhạy cảm.
2. **Batch approval thay vì review từng item.** UI gom các `send_email` có
   cùng confidence band + cùng loại action thành một nhóm, cho phép reviewer
   bấm "Approve tất cả 500" một lần thay vì mở 500 action card riêng lẻ.
3. **Sampling-based review.** Với action có lịch sử độ chính xác cao và rủi ro
   thấp, chỉ route ngẫu nhiên một tỉ lệ nhỏ (vd. 5–10%) cho human review làm
   spot-check, số còn lại auto-execute kèm audit log đầy đủ để truy vết nếu
   phát hiện sai sót sau này.
4. **Calibrate lại confidence score** (xem Câu 3) để phân phối điểm không bị
   dồn cục sát ngưỡng — nguyên nhân gốc rễ thường là do confidence chưa được
   calibrate đúng, không phải do threshold sai.
5. **Escalate theo bất thường, không theo khối lượng.** Chỉ đẩy lên human khi
   có tín hiệu bất thường thực sự (khách hàng thuộc segment nhạy cảm, nội dung
   email khác biệt so với template chuẩn...) thay vì escalate hàng loạt chỉ vì
   confidence thấp hơn một con số cố định.

## Câu 3 — Vì sao không nên chỉ tin confidence tự báo của LLM?

**Vấn đề:** Agent tự báo `confidence_score = 0.95` cho `increase_credit_limit`
nhưng lại thường xuyên sai về thu nhập thực tế của khách hàng. Điều này cho
thấy confidence do LLM tự sinh ra:

- Là sản phẩm phụ của quá trình generate token (dựa trên "nghe có vẻ chắc
  chắn"), **không phải xác suất đã được calibrate** dựa trên độ chính xác
  thực tế.
- Không được grounded vào dữ liệu gốc — model có thể hallucinate một con số
  thu nhập rồi vẫn tự tin 0.95 về quyết định dựa trên con số sai đó.
- Không có cơ chế nào đảm bảo confidence tương quan với tỉ lệ đúng thực tế
  (well-calibrated) — LLM nổi tiếng có xu hướng overconfident, đặc biệt với
  các con số định lượng.

**Cách calibrate trước bước routing:**

1. **Validation node độc lập:** thêm một node kiểm tra factual claims (vd.
   thu nhập/TOI agent dùng) đối chiếu với dữ liệu gốc từ CRM/data warehouse
   trước khi tới `route_action`. Nếu sai lệch vượt ngưỡng, hạ confidence hoặc
   ép force escalate bất kể agent tự báo gì.
2. **Calibration curve từ dữ liệu lịch sử:** theo dõi outcome thực tế (approve
   rate, kết quả sau approve) theo từng action type, dùng Platt scaling /
   isotonic regression để map raw confidence của LLM sang một xác suất đã
   calibrate, rồi dùng điểm đã calibrate cho routing thay vì điểm thô.
3. **Self-consistency / ensemble:** chạy agent nhiều lần (hoặc nhiều
   reasoning path) trên cùng input, dùng tỉ lệ đồng thuận giữa các lần chạy
   làm tín hiệu confidence bổ sung, đáng tin hơn một con số tự agent báo ra.
4. **Tách riêng "confidence về hành động" và "confidence về dữ liệu đầu vào".**
   Một hard rule bổ sung: nếu chất lượng/độ đầy đủ dữ liệu đầu vào (evidence
   completeness) thấp, tự động hạ confidence trước khi đưa vào `route_action`,
   không để LLM tự quyết định toàn bộ.
