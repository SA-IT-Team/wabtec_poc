# Ballooned Drawing Dimension & Tolerance Data Extraction

**Product Requirements Document**
Doc No. BDX-PRD-001 · Rev A — Draft · Date 2026-08-26 · Prepared by Product & BA

> **Implementation note:** [`wabtec_poc`](../wabtec_poc) now implements FR-21 through FR-26 (Reconciliation & QC, §7) — the requirement this document's Executive Summary and §2 name as the core guarantee — in simplified, single-reviewer form. See the per-FR status notes in §7 and [`architecture-poc.md`](architecture-poc.md) §1.3/§1.4/§3.2 for exactly what's implemented and what's still simplified versus this document's target state.

---

## 1. Executive Summary

The system converts ballooned engineering drawings into a fully reconciled, structured Excel record — every balloon number correctly mapped to its dimension, tolerance, and GD&T data — so quality teams can produce accurate first-article and inspection reports in a fraction of the time a manual transcription takes, with zero tolerance for silent data errors.

## 2. Goals & Objectives

- **Eliminate manual transcription error.** Replace hand-keying of dimensions and tolerances from drawing to spreadsheet — the largest source of FAI/PPAP rework — with a verified, traceable extraction pipeline.
- **Cut cycle time per drawing.** Reduce the time to produce a ballooned characteristic list from hours to minutes for drawings of typical complexity (50–150 balloons).
- **Guarantee 100% balloon-to-data reconciliation.** No record leaves the system unless every balloon on the drawing has a matching row, and every row traces back to a specific balloon and drawing zone.
- **Produce audit-ready output.** Every exported Excel file must be defensible in a customer or regulatory audit: who extracted it, who verified it, against which drawing revision.
- **Fit existing quality workflows.** Output structure should map cleanly onto standard forms already in use (e.g., AS9102 Form 3, PPAP ballooned drawing / characteristic matrix) rather than force a new format on downstream consumers.

## 3. User Personas & Core Use Cases

| Persona | Role | Core use case |
|---|---|---|
| **FAI / Quality Analyst** (primary user) | Uploads ballooned drawings, runs extraction, and works balloon-by-balloon to confirm nominal values, tolerances, and GD&T callouts before handoff. | Turn a 120-balloon aerospace drawing into a complete Form 3 characteristic list in under an hour, with confidence every value is correct. |
| **QC Reviewer / Inspector** (verifier) | Performs the independent second-pass reconciliation against the source drawing before a package can be signed off and exported. | Step through every flagged or auto-extracted balloon side-by-side with the drawing and either confirm or correct it, with disagreements logged. |
| **Quality / Program Manager** (owner) | Doesn't touch individual balloons — needs status across a batch of drawings, sign-off history, and exportable packages for a customer submission. | Confirm all drawings for a submission package are reconciled and signed off, then export the full package on demand. |

## 4. In-Scope & Out-of-Scope

### In Scope
- Ingesting ballooned drawings supplied as PDF or high-resolution raster image (TIFF/PNG/JPEG)
- Detecting existing balloon annotations and OCR-reading their numbers
- Extracting linear, angular, and geometric dimensions with their tolerance values (bilateral, unilateral, limit, GD&T frames)
- Mapping each balloon to drawing metadata: drawing number, revision, sheet, zone
- Structured Excel export against a configurable template
- Manual correction UI with side-by-side drawing/data view
- Independent second-reviewer reconciliation workflow with sign-off gate
- Full audit trail (who extracted, who verified, timestamps, source file version)

### Out of Scope (v1)
- Auto-ballooning drawings that arrive *without* existing balloons (treated as an open question — see §14)
- Native CAD file parsing (SolidWorks, NX, CATIA) — PDF/raster only in v1
- Direct CMM program generation from extracted data
- Automated GD&T compliance checking or design validation
- Redlining or markup of the drawing itself
- Multi-language OCR beyond English-language title blocks and notes
- Full PLM/ERP write-back integration (read-only reference links only)

## 5. Stakeholders

| Stakeholder | Interest |
|---|---|
| Quality Analysts / FAI Team | Daily users; need speed and low friction correcting extraction results. |
| QC Reviewers / Inspectors | Own the reconciliation gate; need a fast, reliable comparison view. |
| Quality Manager | Accountable for on-time, defensible submission packages; needs batch visibility. |
| Customer / Auditor (external) | Consumes the exported Excel; needs traceability back to the exact drawing revision. |
| Engineering / Drawing Owner | Source-of-truth authority on the drawing; escalation point for ambiguous callouts. |
| IT / Security | Responsible for protecting proprietary and potentially export-controlled drawing IP. |
| Product / Engineering (build team) | Owns delivery of extraction accuracy, UI, and export fidelity against this document. |

## 6. Assumptions & Constraints

**Assumptions**
- Drawings arrive already ballooned by the customer or a prior process step.
- Drawings follow ASME Y14.5 or ISO 1101 GD&T conventions.
- Source files are text-layer PDFs or scans of at least 300 DPI; lower-quality scans degrade extraction accuracy and route to manual entry.
- One Excel template family (configurable per customer/program) covers initial launch; exotic customer-specific layouts are handled by template configuration, not code changes.
- Human review remains mandatory for sign-off in v1 — extraction accelerates data entry, it does not replace verification.

**Constraints**
- Drawings are proprietary and, for defense/aerospace programs, potentially export-controlled (ITAR/EAR) — this bounds hosting, storage location, and any third-party AI/OCR service used.
- The 100% reconciliation goal (§2) means no automated shortcut may bypass the second-reviewer gate before export.
- Extraction quality is bounded by source scan quality; the system cannot guarantee accuracy on illegible source material — it must detect and flag it instead.

## 7. Functional Requirements

Numbered as `FR-##`. Priority: **MUST** ships in v1, **SHOULD** targeted for v1 if feasible, **COULD** candidate for a later release.

**Drawing ingestion & management**

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | The system shall accept drawing uploads in PDF, TIFF, PNG, and JPEG, individually or as a batch (.zip). | MUST |
| FR-02 | On upload, the system shall extract title-block metadata (drawing number, revision, sheet count, title) via OCR and present it for user confirmation before processing continues. | MUST |
| FR-03 | The system shall reject or flag files below a configurable resolution/quality threshold rather than silently processing them. | MUST |
| FR-04 | The system shall version drawings by revision letter/number and prevent a newer revision from silently overwriting a prior reconciled record — it must create a new linked version instead. | MUST |
| FR-05 | The system shall support multi-sheet drawings, preserving sheet number against every extracted balloon. | MUST |

**Balloon detection & mapping**

| ID | Requirement | Priority |
|---|---|---|
| FR-06 | The system shall automatically detect existing balloon annotations (circled/flagged numbers) on the drawing and OCR-read each balloon's number. | MUST |
| FR-07 | The system shall record, for every detected balloon, its pixel/coordinate location and drawing zone reference, so it can be re-displayed on the source image during review. | MUST |
| FR-08 | The system shall detect duplicate balloon numbers on a single drawing and flag them for user resolution rather than silently keeping one. | MUST |
| FR-09 | The system shall allow a user to manually add, move, renumber, or delete a balloon mapping when auto-detection misses or misreads one. | MUST |
| FR-10 | The system shall report, per drawing, the count of balloons detected on the image versus rows in the extracted table, and block export while the two disagree. | MUST |

**Dimension & tolerance extraction**

| ID | Requirement | Priority |
|---|---|---|
| FR-11 | For each balloon, the system shall extract the associated nominal dimension value and its unit (mm/in), inferring units from the title block when not explicit on the callout. | MUST |
| FR-12 | The system shall extract tolerance data in whatever form it is expressed: bilateral (±), unilateral (+/− asymmetric), limit dimensions (max/min), or a referenced general-tolerance block. | MUST |
| FR-13 | The system shall parse GD&T feature control frames — characteristic symbol, tolerance value, modifiers (MMC/LMC/RFS), and datum references — into discrete structured fields, not a single free-text string. | MUST |
| FR-14 | The system shall capture a confidence score per extracted field and visually distinguish low-confidence extractions in the review UI. | MUST |
| FR-15 | The system shall capture supplementary callout data where present: surface finish, material note, reference/basic dimension flags, and free-text notes tied to a balloon. | SHOULD |
| FR-16 | Where a dimension applies a general/block tolerance rather than an explicit one, the system shall resolve and record the applicable tolerance value, with the source rule cited. | SHOULD |

**Structured export**

| ID | Requirement | Priority |
|---|---|---|
| FR-17 | The system shall export reconciled data to Excel using a configurable column-mapped template (e.g., AS9102 Form 3 layout) selectable per customer or program. | MUST |
| FR-18 | The system shall block export of any drawing package that has not reached full reconciliation and sign-off (see §10). | MUST |
| FR-19 | Each exported row shall include its source balloon number, drawing number, revision, sheet, and zone reference — no orphaned or unattributed rows. | MUST |
| FR-20 | The system shall support exporting a batch of drawings (a full submission package) as one workbook or one file per drawing, per user choice. | SHOULD |

**Reconciliation & QC**

| ID | Requirement | Priority | POC status |
|---|---|---|---|
| FR-21 | The system shall provide a side-by-side view of the source drawing (zoomable, pannable) and the extracted data row for every balloon during review. | MUST | **Partial.** `GET .../reconciliation` returns every balloon's extracted/reviewed data side by side in `wabtec_poc_app`'s ReconciliationPanel; there is no zoomable/pannable *image* of the source drawing in the review UI — only the extracted values, not a rendering of the drawing itself. |
| FR-22 | The system shall require a second, independent reviewer (not the original extractor) to confirm or correct every balloon before the drawing can be marked reconciled. | MUST | **Implemented, with a caveat.** Segregation of duties is enforced server-side (`reviewerId` ≠ the drawing's `submittedBy`) — but both identities are self-declared strings, not authenticated accounts. See architecture-poc.md §3.3. |
| FR-23 | Where the reviewer's value differs from the extracted value, the system shall log both values, the reviewer identity, and require a resolution before the balloon is considered closed. | MUST | **Implemented, simplified.** Both values, the reviewer, and a `discrepancy` flag are recorded the moment a correction is submitted — resolution is immediate, one review pass, not a separate open/resolve step. This document's "resolution" language describes a two-human-disagreement model this POC doesn't implement; see architecture-poc.md §1.4 trade-off #3. |
| FR-24 | The system shall present a completion indicator showing count of balloons reviewed versus total, and prevent sign-off until 100% are reviewed. | MUST | **Implemented.** `reconciliation.percentComplete`/`readyForSignoff`; sign-off returns `409` with the exact open-balloon list otherwise. |
| FR-25 | The system shall let a reviewer flag a balloon as "cannot determine from source" and route it to the drawing owner rather than force a guess. | MUST | **Partial.** The flag exists (`cannot_determine`, with a required reason) and correctly still blocks sign-off — but nothing routes it to a drawing owner; it just sits flagged until someone re-reviews it. |
| FR-26 | Sign-off shall capture the signer's identity, timestamp, and drawing revision, and lock the reconciled record against further silent edits. | MUST | **Implemented.** Signer, timestamp, and revision are stamped on sign-off, and the record rejects further review calls once `signed_off` is true (a lock added beyond this requirement's literal text, once it was clear an unlocked record could otherwise drift after sign-off). |

**Traceability, audit & reporting**

| ID | Requirement | Priority |
|---|---|---|
| FR-27 | The system shall maintain a full audit log per drawing: uploads, extraction runs, edits, reviewer actions, and sign-off, each with actor and timestamp. | MUST |
| FR-28 | The system shall let a user search and filter drawings by number, revision, status (in progress / reconciled / signed off), and assigned reviewer. | SHOULD |
| FR-29 | The system shall provide a batch/program-level status view showing reconciliation progress across all drawings in a submission package. | SHOULD |
| FR-30 | The system shall allow re-export of a previously signed-off package without re-running reconciliation, reproducing the exact locked data. | COULD |

## 8. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Performance | Extraction of a typical 100-balloon drawing (clean text-layer PDF) completes in under 2 minutes end-to-end. The review UI renders drawing pan/zoom interactions with no perceptible lag on files up to 50 MB / A0-size sheets. |
| Scalability | The system supports batch ingestion of at least 200 drawings per submission package without degrading per-drawing processing time, via queued/parallel extraction. |
| Availability | Core review and export functions target 99.5% uptime during business hours; a queued extraction job survives a transient service restart without data loss. |
| Accuracy | Balloon detection recall/precision and field-level extraction accuracy are measured against a golden test set (see §11) and published as a tracked metric — the 100% accuracy goal in §2 is achieved by the human reconciliation gate, not claimed of automated extraction alone. |
| Usability | A reviewer can complete confirm/correct on one balloon in a small number of interactions (target: under 10 seconds per already-correct balloon) — the review UI is the primary driver of overall cycle time. |
| Accessibility | The review and reporting UI meets WCAG 2.1 AA for keyboard navigation and color contrast; extracted-value flags (confidence, discrepancy) are not conveyed by color alone. |
| Compliance | Output structure is compatible with AS9102 Rev C (Form 3) and common PPAP ballooned-drawing formats; the system supports the retention periods those standards require for inspection records. |
| Localization | Supports both metric and imperial units and both ASME Y14.5 and ISO 1101 GD&T symbol sets within the same install. |
| Maintainability | The Excel export template is configuration-driven (column mapping, form layout) rather than hard-coded, so a new customer template does not require a code release. |

## 9. Security Requirements

- **Data classification & IP protection.** Drawings are treated as confidential engineering IP by default; access to any drawing is scoped to users explicitly assigned to its program.
- **Export control.** The system supports marking drawings/programs as export-controlled (ITAR/EAR); such data is restricted to authorized-national users and, where required, to approved hosting regions — this bounds which OCR/AI processing services are permissible (see §14).
- **Encryption.** All drawing files and extracted data are encrypted at rest and in transit (TLS 1.2+).
- **Authentication & access control.** SSO with MFA for authentication; role-based access control distinguishing Analyst, Reviewer, Manager, and Admin, enforced at the API layer, not just the UI.
- **Segregation of duties.** The system technically enforces that the reconciliation reviewer (FR-22) cannot be the same account that performed the original extraction/edit on that balloon.
- **Audit logging.** Security-relevant events (login, permission change, export, download) are logged immutably and retained per the applicable compliance retention period.
- **Upload safety.** All uploaded files are scanned for malware before processing; unsupported or malformed files are rejected with a clear reason, not silently dropped.
- **Third-party AI/OCR services.** Any third-party model or service used for extraction must contractually exclude customer drawing data from training data retention, and must be identified in a data processing addendum.
- **Data retention & deletion.** Retention periods are configurable per program/customer requirement; deletion requests remove source files and derived data while preserving the audit log entry that a deletion occurred.
- **Export watermarking (optional).** Exported Excel packages may be watermarked/labeled with recipient and export timestamp for leak traceability, where the customer requires it.

## 10. High-Level Workflow

Every drawing moves through the same gated sequence; nothing reaches export without passing the reconciliation gate.

1. **Upload & validate** — User uploads drawing(s); system confirms title-block metadata and revision, rejects sub-threshold quality files.
2. **Balloon detection** — System locates and numbers every balloon on the drawing; count is recorded as the target row count.
3. **Automated extraction** — For each balloon, the system drafts nominal value, tolerance, and GD&T fields with a confidence score.
4. **Analyst review** — Analyst works the side-by-side view, confirming or correcting every balloon — starting with low-confidence flags.
5. **Independent reconciliation** — A second reviewer re-checks every balloon against the source drawing, independent of the analyst's pass. *Gate — all balloons must match.*
6. **Discrepancy resolution** — Any mismatch between analyst and reviewer routes back to step 4, or escalates to the drawing owner if unresolved.
7. **Sign-off** — Reviewer signs off; the reconciled record is locked and stamped with signer identity, timestamp, and revision. *Gate — export disabled until signed.*
8. **Export & archive** — Structured Excel is generated from the locked record; source drawing, extracted data, and audit log are archived together.

## 11. Testing Requirements

- **Golden-set accuracy testing.** A curated set of drawings spanning quality tiers (clean CAD-native PDF, average scan, poor scan, hand-annotated) with verified ground truth, re-run on every extraction model/pipeline change to track balloon-detection and field-level accuracy over time.
- **Unit testing.** Parsers for dimension formats, tolerance notations, and GD&T frames are unit-tested against a representative library of real-world notation variants.
- **Integration testing.** End-to-end: upload → extraction → review → reconciliation → export, verifying the exported workbook matches the locked record exactly, for each supported template.
- **Template regression testing.** Adding or editing an export template must not alter output for existing templates; run the full export suite on every template change.
- **User acceptance testing.** Quality analysts and reviewers test against real (or representative) drawings from at least two industries/programs before general availability, validating both accuracy and review-time targets from §8.
- **Security testing.** Access-control tests confirming role boundaries and segregation-of-duties enforcement (§9); periodic penetration testing on upload and export endpoints.
- **Performance/load testing.** Batch upload of 200 drawings and concurrent multi-user review sessions tested against the targets in §8.
- **Accessibility testing.** Automated and manual WCAG 2.1 AA checks on the review UI, including keyboard-only completion of a full reconciliation pass.

## 12. Edge Cases & Error Handling

| Scenario | Required handling |
|---|---|
| Illegible or very low-resolution scan | Flagged at upload (FR-03); balloons on that page route directly to manual entry rather than a low-confidence guess. |
| Balloon present on drawing but not detected by OCR | Row-count check (FR-10) surfaces the mismatch before export is allowed; user adds it manually (FR-09). |
| Duplicate balloon numbers on one sheet | Flagged for resolution (FR-08); export blocked until resolved. |
| Balloon referencing a dimension with no visible tolerance (general tolerance applies) | System resolves against the title-block general-tolerance note (FR-16) and cites the source; if no general-tolerance block exists, flags for manual input. |
| Hand-written or freehand balloon/callout | Lower automatic confidence score by design; always routed through manual confirmation, never auto-accepted. |
| Overlapping or crowded balloons (dense drawing zones) | Reviewer can zoom the source pane past the auto-detected region; manual reposition supported (FR-09). |
| Mixed unit drawing (some mm, some inch dimensions) | Per-field unit is captured explicitly (FR-11), never assumed globally from the title block alone. |
| Drawing revision changes mid-reconciliation | New revision creates a linked new version (FR-04); in-progress reconciliation on the old revision is preserved but flagged stale, not silently merged. |
| Reviewer disagrees with analyst on a value | Both values and identities logged (FR-23); balloon stays open until explicitly resolved — never auto-resolved by "last edit wins." |
| Upload interrupted mid-transfer / corrupted file | Upload is validated post-transfer (checksum); a failed validation surfaces a specific, actionable error and prompts re-upload — no partial file is queued for extraction. |
| Export attempted before sign-off | Blocked at the action level (FR-18), not just hidden in the UI — attempting via any interface returns an explicit "not reconciled" error. |
| Extraction service (OCR/AI) unavailable or times out | Job is queued and retried; user sees job status rather than a silent failure, and can still perform fully manual entry in the meantime. |

## 13. Acceptance Criteria

**Balloon detection on upload**
> **GIVEN** a valid, adequately-scanned ballooned drawing is uploaded
> **WHEN** automated processing completes
> **THEN** every visually distinct balloon on the drawing has exactly one corresponding draft row, and the detected-count matches the on-image balloon count shown to the user

**Low-confidence extraction is surfaced, not hidden**
> **GIVEN** a field's extraction confidence falls below the configured threshold
> **WHEN** the analyst opens the review UI
> **THEN** that balloon is visually flagged and sorted ahead of high-confidence balloons for review

**Independent reconciliation gate**
> **GIVEN** an analyst has completed their pass on a drawing
> **WHEN** a second reviewer opens it for reconciliation
> **THEN** the system requires that reviewer's account to differ from the analyst's account, and will not allow sign-off from the same identity

**Discrepancy logging**
> **GIVEN** a reviewer enters a value that differs from the analyst's extracted or confirmed value for a balloon
> **WHEN** the reviewer saves that balloon
> **THEN** both values and both user identities are recorded, and the balloon is marked "unresolved" until an explicit resolution action is taken

**Export blocked until fully reconciled**
> **GIVEN** one or more balloons on a drawing are not yet marked reconciled
> **WHEN** any user attempts to export that drawing
> **THEN** the export is refused with a message identifying which balloons remain open

**Export fidelity**
> **GIVEN** a drawing has been fully reconciled and signed off
> **WHEN** the user exports it to Excel
> **THEN** every row in the workbook matches the locked reconciled record exactly — value, tolerance, GD&T fields, and balloon/drawing/revision/zone reference — with no row added, dropped, or altered by the export step

## 14. Open Questions

These materially affect scope and architecture and should be resolved before implementation planning.

1. **Input drawings — pre-ballooned only, or does the system also need to balloon un-marked drawings?** The raw brief says "drawings containing ballooned dimensions," which this document treats as pre-ballooned input (§4). If un-ballooned drawings are in scope, an auto-ballooning capability is a significant additional workstream.
2. **Source format — is native CAD (SolidWorks, NX, CATIA, DXF) required, or is PDF/raster sufficient?** This document scopes v1 to PDF/raster only.
3. **Which Excel template(s) must be supported at launch?** AS9102 Form 3 and a generic PPAP-style characteristic matrix are assumed as candidates — please confirm the actual target template(s) and whether per-customer variants are needed.
4. **Deployment model:** cloud/SaaS, customer on-premise, or both? This is the primary driver for the export-control and hosting-region requirements in §9.
5. **Is any third-party OCR/AI extraction service pre-approved, or does the vendor need to be selected/vetted as part of this project?** This directly affects the export-control constraint in §6/§9.
6. **Expected volume:** drawings per day/week and typical balloon count per drawing, to size performance targets in §8 correctly.
7. **Is integration with an existing PLM/QMS/ERP system (e.g., pulling drawing metadata, writing back inspection status) required for v1, or is the tool standalone?**
8. **Who performs the "independent reviewer" role in practice** — a second internal analyst, a dedicated QC function, or in some cases an outsourced/BPO team? This affects the access-control model in §9.
9. **Should the tool support GD&T standards beyond ASME Y14.5 and ISO 1101 (e.g., older or industry-specific variants)?**
10. **What is the acceptable automated extraction accuracy floor before a drawing is routed entirely to manual entry rather than assisted review?** A numeric target is needed to tune the confidence-threshold behavior in FR-14 and §8.

---
*Ballooned Drawing Dimension & Tolerance Data Extraction — Requirements Baseline, Rev A (Draft). Prepared for internal review; not yet approved for implementation.*
