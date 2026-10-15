
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

#### Query Policy Knowledge Base
Allows operations employees to query internal banking rules and receive grounded responses with citation metadata.

* Endpoint: `POST /api/v1/policies/ask`
* Request Body (`application/json`):
```json
{
  "query": "What are the required proof-of-address documents for updating corporate data?"
}
```
* Response (`200 OK`):
```json
{
  "answered": true,
  "response_text": "According to the KYC Policy v2.1, acceptable proof of address documents include utility bills not older than 3 months or a certified lease agreement.",
  "citation": {
    "policy_name": "Retail_KYC_Policy_Guidelines.pdf",
    "version": "v2.1",
    "section": "3.2"
  }
}
```

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
