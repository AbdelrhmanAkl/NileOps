# Complaints API (Simulation)

Base path: `/api/v1/banking/complaints`

Complaints are classified and routed with **deterministic keyword rules**
(`src/services/complaint_router.py`). No AI model is involved and no message is
sent to customers. `suggested_reply` is a **draft** for an employee to review.
Low-confidence complaints stop at `classified` and wait for human triage.

## Roles

| Action | viewer | reviewer | admin |
|---|:--:|:--:|:--:|
| List, detail, summary | yes | yes | yes |
| Create, assign, change status | no | yes | yes |

## Status lifecycle

```
(create) -> assigned            confident rule match
         -> classified          low confidence, waits for human triage
classified | assigned -> assigned      POST /assign (human triage or re-route)
assigned -> in_progress -> resolved -> closed
resolved -> in_progress                reopen
```

`/status` only accepts `in_progress`, `resolved`, `closed`. `new`, `classified`
and `assigned` are system/assignment states. Re-assignment is blocked once
processing has started (`in_progress`, `resolved`, `closed`) and returns `409`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/` | Create, classify, route (201) |
| GET | `/` | List with `q`, `status`, `priority`, `category`, `department`, `page`, `page_size` |
| GET | `/summary` | Counts by status, priority, category, department (dashboard) |
| GET | `/{id}` | Detail + audit history + `reply_is_draft` |
| POST | `/{id}/assign` | Human triage or re-route to a department |
| POST | `/{id}/status` | Move to `in_progress`, `resolved` or `closed` |

### Create

```json
{
  "customer_id": 1,
  "description": "العميل يواجه مشكلة في استخدام البطاقة",
  "category": "بطاقات"
}
```

`customer_id` and `category` are optional. Unknown fields are rejected (`422`).
Allowed categories: `بطاقات`, `حسابات`, `تحويلات`, `خدمة العملاء`, `احتيال وأمان`.

### Assign

```json
{
  "department": "عمليات التحويلات",
  "priority": "high",
  "notes": "تم الفرز بعد مراجعة النص"
}
```

## Classification rules (summary)

- Category = the category with the highest keyword score. Generic words such as
  "حساب" count half. A tie or no match means human triage.
- Security words (theft, hacking, unknown transactions, suspicious messages)
  route to `إدارة مكافحة الاحتيال` with `urgent` priority.
- Base priority: cards `high`, accounts `medium`, transfers `urgent`,
  customer service `low`. Words like "عاجل" raise it one level. Double-charge
  wording sets `urgent` without treating it as fraud.
- Matching normalizes Arabic letters and diacritics and strips common prefixes
  ("ال", "وال", "بال", ...).

## Audit trail

Every action writes a `BankingAuditEvent` (`entity_type = "complaint"`):
`COMPLAINT_CREATED`, `AUTO_CLASSIFIED`, `HUMAN_ASSIGNMENT`, `STATUS_CHANGE`.
Complaint text is deliberately **not** copied into audit details.

## Limits and notes

- Keyword rules are a starting point. Measure them on a labelled set before
  relying on the numbers in the project handbook.
- `description` may contain personal data in real use; apply your retention
  policy before moving beyond synthetic data.
