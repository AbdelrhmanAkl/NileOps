# Project Objectives, Scope, and Boundaries Documentation

## 1. Project Objectives
The primary objective of the NileOps MVP platform is to empower digital banking operations personnel by introducing AI-driven automation for routine tasks while maintaining strict human governance and auditability[cite: 1, 2].

* Reduce Processing Time: Minimize the turnaround time for routine completed requests by at least 30% compared to traditional manual baselines[cite: 5].
* Automate Low-Risk Workflows: Achieve at least 60% straight-through processing (STP) for qualified low-risk synthetic cases within the sandbox environment[cite: 5, 16].
* Improve Data Quality: Ensure high Document AI extraction accuracy with a macro F1 score of at least 90 for agreed-upon fields[cite: 5, 16].
* Optimize Exception Handling: Correctly classify and escalate at least 95% of anomalous, low-confidence, or exception cases to the appropriate human review queues[cite: 5, 16].
* Ensure Policy Compliance: Deliver grounded RAG-based answers to staff queries with at least 90% accuracy and proper source attribution, enforcing strict abstention when data is missing[cite: 5, 16].
* Maintain Complete Auditability: Log 100% of state transitions, decision events, and policy/model versions in an immutable audit trail free of personal identifiable information (PII)[cite: 6, 16].

---

## 2. In-Scope Features and Workflows
The MVP strictly focuses on four simulation scenarios handled within an isolated sandbox environment using synthetic data:

* Synthetic KYC and Data Update:
  * Extracting core fields (full name, address, synthetic ID number, and expiry date) from designated synthetic documents using Document AI/OCR[cite: 3, 6].
  * Tracking source attribution and confidence scores per field[cite: 3].
* Simulated Account Opening:
  * Checking data completeness and cross-matching documents[cite: 6].
  * Creating draft requests and routing them to human review queues (without creating actual live accounts)[cite: 6].
* Simulated Card Request:
  * Registering requests, routing them to test queues, and displaying real-time tracking states (without card issuance or shipping)[cite: 6].
* Complaint Reception and Triage:
  * Categorizing limited complaint types (e.g., delays or data errors)[cite: 6].
  * Suggesting the appropriate internal team and displaying reviewable draft response templates[cite: 6].
* Operational Support Tools:
  * RAG policy assistant for internal guidelines with mandatory version and source citations[cite: 3].
  * Human-in-the-loop review interface allowing authorized staff to approve, reject, or request missing information[cite: 3, 5].

---

## 3. Out-of-Scope Boundaries
To maintain security, compliance, and controlled MVP execution, the following elements are strictly excluded from the project:

* Production Core Integration:
  * Any connection, read, or write operations to live production Core Banking systems, Card Management systems, or CRM databases[cite: 7].
* Financial Transactions:
  * Executing actual money transfers, account balance modifications, or live financial movements[cite: 7].
* Credit and Risk Decisions:
  * Making credit scoring decisions, loan pricing computations, or final customer risk-tier approvals[cite: 7].
* Final Compliance Authorizations:
  * Executing final KYC/AML sign-offs, live sanctions screening against watchlists (PEP/Sanctions), generating or submitting Suspicious Transaction Reports (STRs), or issuing formal regulatory notifications[cite: 7].
* Real Customer Data:
  * Storing, processing, or utilizing real customer documents or PII without explicit written authorization and legal/data protection review (the system defaults exclusively to synthetic test data)[cite: 7].
* Public Deployment and Autonomous Agents:
  * Public internet deployment, direct consumer-facing chat interfaces with live clients, or granting AI agents write permissions over banking environments[cite: 7].
* Complex Financial Products:
  * Incorporating trade finance, syndicated loans, documentary credits, or comprehensive financial auditing workflows into this version[cite: 7].
