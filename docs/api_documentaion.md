
## API Documentation

### 1. Request Intake and Management

#### Submit New Request
Initiates a new operational ticket by selecting the request type and attaching synthetic files.

* Endpoint: `POST /api/v1/requests`
* Request Body (`multipart/form-data`):
  * `request_type` (string, required): `kyc_update` | `account_opening` | `card_request` | `complaint`
  * `customer_id` (string, required): Synthetic customer identifier.
  * `documents` (array of files, required): Synthetic documents (PDFs/Images).
* Response (`201 Created`):
```json
{
  "success": true,
  "request_id": "req_987654321",
  "request_type": "kyc_update",
  "status": "pending_extraction",
  "created_at": "2026-10-15T10:00:00Z"
}
```

---

### 2. Document AI and Extraction Review

#### Get Request Details
Retrieves general status and tracking details for a specific request.

* Endpoint: `GET /api/v1/requests/{id}`
* Response (`200 OK`):
```json
{
  "request_id": "req_987654321",
  "request_type": "kyc_update",
  "current_status": "in_review",
  "assigned_queue": "compliance_tier_1",
  "updated_at": "2026-10-15T10:02:00Z"
}
```

#### Get Extracted Fields and Confidence Scores
Fetches field values parsed by the Document AI service along with confidence scores and source mapping.

* Endpoint: `GET /api/v1/requests/{id}/extracted-fields`
* Response (`200 OK`):
```json
{
  "request_id": "req_987654321",
  "extraction_summary": {
    "confidence_average": 0.94,
    "has_mismatches": false,
    "missing_fields": []
  },
  "fields": [
    {
      "field_name": "full_name",
      "value": "Nour El-dein Ahmed Mansour",
      "confidence_score": 0.98,
      "source": {
        "document_name": "national_id_synthetic.pdf",
        "page": 1
      }
    },
    {
      "field_name": "id_expiry_date",
      "value": "2030-05-12",
      "confidence_score": 0.91,
      "source": {
        "document_name": "national_id_synthetic.pdf",
        "page": 1
      }
    }
  ]
}
```

---

### 3. Policy Assistant (RAG)

Answers questions from the approved policy files in `knowledge/policies/`, always with
citations (policy code, version, section). When the policies do not cover the question
the assistant **abstains** instead of guessing. Answers are advisory; a human decides.
See `docs/policies_rag.md` for setup (including the local Qwen model).

#### Query Policy Knowledge Base
* Endpoint: `POST /api/v1/policies/ask` (roles: viewer, reviewer, admin)
* Request Body: `{"question": "..."}` (5–500 characters; `"query"` is accepted as an alias)
* Response (`200 OK`):
```json
{
  "status": "answered",
  "mode": "extractive",
  "answer": "تُسلَّم البطاقة الجديدة خلال 7 أيام عمل من تاريخ اعتماد الطلب. ... [1]",
  "coverage": 0.83,
  "citations": [
    {
      "number": 1,
      "chunk_id": 12,
      "policy_code": "POL-CARD-001",
      "version": 2,
      "title": "سياسة إصدار البطاقات",
      "heading": "مدة التسليم",
      "excerpt": "تُسلَّم البطاقة الجديدة خلال 7 أيام عمل ...",
      "score": 5.412
    }
  ],
  "note": null,
  "advisory": "إجابة استرشادية من سياسات البنك؛ القرار النهائي للموظف المختص."
}
```
* `status`: `answered` or `abstained` (no citations when abstained).
* `mode`: `extractive` (text taken from the policy), `llm` (rephrased by the local model
  and verified against the cited passages) or `none` (abstained before any model call).

#### Browse policies
* `GET /api/v1/policies?include_superseded=false` — active policy versions.
* `GET /api/v1/policies/{policy_code}?version=N` — sections of the latest (or a given) version.
* `POST /api/v1/policies/reload` (admin) — load new/changed files from the policies folder.
  A new `version:` supersedes the older one for answers; old versions stay readable.
  Changing text without bumping `version:` is reported as a conflict and not applied.

---

### 4. Workflow Queues and Human-in-the-Loop

#### Get Operational Queue
Retrieves items sitting in a designated queue awaiting manual intervention.

* Endpoint: `GET /api/v1/queues/{queue_name}`
* Response (`200 OK`):
```json
{
  "queue_name": "exceptions_queue",
  "total_items": 1,
  "requests": [
    {
      "request_id": "req_123456789",
      "request_type": "account_opening",
      "escalation_reason": "Low confidence score on address field (0.78)",
      "submitted_at": "2026-10-15T09:30:00Z"
    }
  ]
}
```

#### Submit Human Review Decision
Submits employee review actions on flagged requests.

* Endpoint: `POST /api/v1/requests/{id}/review-action`
* Request Body (`application/json`):
```json
{
  "action": "request_info",
  "comment": "Please provide a clearer scan of the ID back page.",
  "employee_id": "emp_402"
}
```
* Response (`200 OK`):
```json
{
  "success": true,
  "request_id": "req_123456789",
  "new_status": "pending_customer_completion",
  "updated_at": "2026-10-15T10:15:00Z"
}
```

---

### 5. Audit Trail

#### Get Request Audit Trail
Returns the complete event timeline of state changes and actions performed on a request.

* Endpoint: `GET /api/v1/requests/{id}/audit-trail`
* Response (`200 OK`):
```json
{
  "request_id": "req_987654321",
  "audit_events": [
    {
      "timestamp": "2026-10-15T10:00:00Z",
      "event_type": "REQUEST_CREATED",
      "actor": "system",
      "details": "Synthetic documents ingested successfully."
    },
    {
      "timestamp": "2026-10-15T10:01:30Z",
      "event_type": "EXTRACTION_COMPLETED",
      "actor": "document_ai_v1",
      "details": "Extracted 5 fields with macro F1 compliance."
    },
    {
      "timestamp": "2026-10-15T10:05:00Z",
      "event_type": "HUMAN_APPROVAL",
      "actor": "emp_402",
      "details": "Request approved after manual exception override."
    }
  ]
}
```
