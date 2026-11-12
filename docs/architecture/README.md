# Architecture diagrams (Milestone 3, Appendix A)

Milestone 3 says its Appendix A holds "the exact sources of every figure, stored in
docs/architecture/". Milestone 1 also commits to keeping the Mermaid.js files in the repository.
These `.mmd` files were recovered from the final Milestone 3 PDF (October 23, 2026), Appendix A.
Page headers and footers were removed, and lines the PDF wrapped were rejoined.

Every file renders with Mermaid CLI 11.4.2, and CI checks this on every push. Successful
rendering proves the syntax is valid. It does not prove that each rejoined line is word-for-word
the team's original file: please compare with the team's Mermaid files if they still exist.

The diagrams describe the design. Where the implementation differs (for example SQLAlchemy and
Tailwind in Diagram 1), see `../INTEGRATION_DECISIONS.md`.

### 01-container

Source: [`01-container.mmd`](01-container.mmd)

```mermaid
flowchart LR
  USER["Business Owner<br/>(phone or desktop browser)"]
  subgraph SYSTEM["AI Forecasting and Organizational Tool"]
    FRONTEND["React SPA container<br/>React 18 + Vite + Tailwind CSS<br/>served by nginx"]
    BACKEND["FastAPI backend container<br/>Python 3.12, pandas, Pillow<br/>validation, privacy, AI calls,<br/>analysis, persistence"]
    DATABASE[("PostgreSQL 16<br/>owners, submissions,<br/>sales records, baselines")]
  end
  STORAGE[("Private object storage<br/>Cloudflare R2 bucket<br/>sanitized images only")]
  AI["Anthropic Claude API<br/>extraction + guidance wording"]
  USER -->|"HTTPS"| FRONTEND
  FRONTEND -->|"HTTPS / REST JSON<br/>Bearer access code"| BACKEND
  BACKEND -->|"SQL over TLS<br/>SQLAlchemy"| DATABASE
  BACKEND -->|"HTTPS / S3 API<br/>put, delete, signed URL"| STORAGE
  BACKEND -->|"HTTPS / REST<br/>Messages API"| AI
  FRONTEND -. "HTTPS GET signed URL<br/>(image preview, 300 s)" .-> STORAGE
```

### 02a-class-capture

Source: [`02a-class-capture.mmd`](02a-class-capture.mmd)

```mermaid
classDiagram
  direction TB
  class SalesCaptureController {
    -inference: InferenceService
    -repository: RecordRepository
    -storage: ObjectStorage
    +extract(ownerId: UUID, saleDate: date, file: Optional~UploadFile~, text: Optional~str~) ExtractResponse
    +confirm(ownerId: UUID, request: ConfirmRequest) ConfirmResponse
    +createManualEntry(ownerId: UUID, request: ManualEntryRequest) ConfirmResponse
    +listOpenSubmissions(ownerId: UUID) list~SubmissionSummary~
    +getSubmission(ownerId: UUID, submissionId: UUID) SubmissionDetail
    +deleteSubmission(ownerId: UUID, submissionId: UUID) None
    +getSalesHistory(ownerId: UUID, start: date, end: date) list~StructuredSalesRecord~
    -validateInput(saleDate: date, file: Optional~UploadFile~, text: Optional~str~) None
    -validateLines(lines: list~ConfirmLine~) None
  }
  class InferenceService {
    -model: VisionModel
    -repository: RecordRepository
    +timeoutSeconds: int = 30
    +maxAttempts: int = 2
    +sanitizeImage(image: bytes) bytes
    +maskText(text: str) str
    +maskContacts(result: ExtractionResult) ExtractionResult
    +extractFromImage(submissionId: UUID, image: bytes) SalesDraft
    +extractFromText(submissionId: UUID, maskedText: str) SalesDraft
    +applyConfidenceBands(submissionId: UUID, result: ExtractionResult) SalesDraft
  }
  class VisionModel {
    <<interface>>
    +extractFromImage(image: bytes, prompt: str, timeout: int) ExtractionResult
    +extractFromText(text: str, prompt: str, timeout: int) ExtractionResult
  }
  class ClaudeVisionModel {
    -client: Anthropic
    -modelName: str
    +extractFromImage(image: bytes, prompt: str, timeout: int) ExtractionResult
    +extractFromText(text: str, prompt: str, timeout: int) ExtractionResult
  }
  class MockVisionModel {
    -cannedResult: ExtractionResult
    -failMode: Optional~str~
    +extractFromImage(image: bytes, prompt: str, timeout: int) ExtractionResult
    +extractFromText(text: str, prompt: str, timeout: int) ExtractionResult
  }
  class ObjectStorage {
    -bucketName: str
    +putObject(data: bytes, contentType: str) str
    +getSignedUrl(key: str, expiresIn: int = 300) str
    +deleteObject(key: str) None

  }
  class RetentionService {
    -repository: RecordRepository
    -storage: ObjectStorage
    +retentionDays: int = 30
    +staleSeconds: int = 120
    +runSweep(now: datetime) None
  }
  class RecordRepository {
    +createSubmission(ownerId: UUID, inputType: InputType, saleDate: date, imageKey: Optional~str~, maskedText: Optional~str~) UUID
    +updateStatus(submissionId: UUID, status: SubmissionStatus) None
    +saveDraft(draft: SalesDraft) None
    +getSubmission(ownerId: UUID, submissionId: UUID) Optional~Submission~
    +listOpenSubmissions(ownerId: UUID) list~Submission~
    +confirmSubmission(ownerId: UUID, submissionId: UUID, lines: list~ConfirmLine~) ConfirmOutcome
    +createManualSubmission(ownerId: UUID, submissionId: UUID, saleDate: date, lines: list~ConfirmLine~) ConfirmOutcome
    +deleteSubmission(ownerId: UUID, submissionId: UUID) Optional~str~
    +getConfirmedRecords(ownerId: UUID, start: date, end: date) list~StructuredSalesRecord~
    +markStale(olderThan: datetime) int
    +purgeExpired(cutoff: datetime) list~str~
  }
  class ExtractionResult {
    +overallConfidence: float
    +lines: list~ExtractedLine~
  }
  class ExtractedLine {
    +itemName: Optional~str~
    +quantity: Optional~Decimal~
    +unitPrice: Optional~Decimal~
    +itemNameConfidence: Optional~float~
    +quantityConfidence: Optional~float~
    +unitPriceConfidence: Optional~float~
  }
  class SalesDraft {
    +submissionId: UUID
    +status: SubmissionStatus
    +message: Optional~str~
    +lines: list~DraftLine~
  }
  class DraftLine {
    +lineId: int
    +itemName: Optional~str~
    +quantity: Optional~Decimal~
    +unitPrice: Optional~Decimal~
    +bands: dict~str, ConfidenceBand~
  }
  class Submission {
    +id: UUID
    +ownerId: UUID
    +inputType: InputType
    +status: SubmissionStatus
    +saleDate: date
    +imageKey: Optional~str~
    +maskedText: Optional~str~
    +draftPayload: Optional~dict~
    +createdAt: datetime
    +updatedAt: datetime
    +confirmedAt: Optional~datetime~
    +records: list~StructuredSalesRecord~
  }
  class StructuredSalesRecord {
    +id: UUID
    +submissionId: UUID
    +lineNo: int
    +itemName: str
    +itemKey: str
    +quantity: Decimal
    +unitPrice: Decimal
    +source: RecordSource
    +saleDate: date
    +lineTotal() Decimal
  }
  class SubmissionStatus {
    <<enumeration>>
    UPLOADED
    CLASSIFYING
    NEEDS_VERIFICATION
    CONFIRMED

    REJECTED
    AI_UNAVAILABLE
  }
  class ConfidenceBand {
    <<enumeration>>
    HIGH
    MEDIUM
    LOW
    UNSCORED
  }
  class InputType {
    <<enumeration>>
    IMAGE
    TEXT
    MANUAL
  }
  class RecordSource {
    <<enumeration>>
    AI_EXTRACTED
    MANUAL
  }
  SalesCaptureController --> InferenceService : keeps
  SalesCaptureController --> RecordRepository : keeps
  SalesCaptureController --> ObjectStorage : keeps
  SalesCaptureController ..> SalesDraft : returns
  InferenceService --> VisionModel : keeps
  InferenceService --> RecordRepository : keeps
  InferenceService ..> ExtractionResult : receives
  InferenceService ..> SalesDraft : creates
  RetentionService --> RecordRepository : keeps
  RetentionService --> ObjectStorage : keeps
  VisionModel <|.. ClaudeVisionModel : implements
  VisionModel <|.. MockVisionModel : implements
  ExtractionResult *-- "0..100" ExtractedLine : owns
  SalesDraft *-- "0..100" DraftLine : owns
  Submission *-- "0..100" StructuredSalesRecord : owns
  RecordRepository ..> Submission : reads and writes
  Submission --> SubmissionStatus : status
  Submission --> InputType : inputType
  DraftLine --> ConfidenceBand : bands
  StructuredSalesRecord --> RecordSource : source
```

### 02b-class-analysis

Source: [`02b-class-analysis.mmd`](02b-class-analysis.mmd)

```mermaid
classDiagram
  direction TB
  class AnalysisController {
    -analyzer: BaselineAnalyzer
    -guidance: GuidanceEngine
    +getAnalysis(ownerId: UUID, asOf: date) AnalysisResult
    +requestGuidance(ownerId: UUID, asOf: date) GuidanceResult
  }
  class BaselineAnalyzer {
    -repository: RecordRepository
    +historyDays: int = 28
    +windowDays: int = 7
    +minLoggedDays: int = 7
    +coverDays: int = 7
    +trendThreshold: float = 0.10
    +computeBaselines(ownerId: UUID, asOf: date) AnalysisResult
    -buildDailyMatrix(records: list~StructuredSalesRecord~) DataFrame
    -averageOf(matrix: DataFrame, days: int, offset: int) Series
    -trend(recentAvg: Decimal, priorAvg: Optional~Decimal~) TrendDirection
    -suggestedQty(avg: Decimal) int
  }
  class GuidanceEngine {
    -llm: LLMService
    +timeoutSeconds: int = 15
    +maxItems: int = 20
    +generateGuidance(estimates: list~BaselineEstimate~) GuidanceResult
    -buildPrompt(estimates: list~BaselineEstimate~) str
    -checkGrounding(raw: str, estimates: list~BaselineEstimate~) GroundingOutcome
    -fallbackTemplate(estimates: list~BaselineEstimate~) list~GuidanceItem~
  }
  class LLMService {
    <<interface>>
    +generateJson(prompt: str, schema: dict, timeout: int) str
  }
  class ClaudeLLMService {

    -client: Anthropic
    -modelName: str
    +generateJson(prompt: str, schema: dict, timeout: int) str
  }
  class MockLLMService {
    -cannedText: str
    -failMode: Optional~str~
    +generateJson(prompt: str, schema: dict, timeout: int) str
  }
  class RecordRepository {
    +getConfirmedRecords(ownerId: UUID, start: date, end: date) list~StructuredSalesRecord~
    +replaceBaselines(ownerId: UUID, asOfDate: date, estimates: list~BaselineEstimate~) None
    +deleteBaselines(ownerId: UUID) None
  }
  class StructuredSalesRecord {
    +itemName: str
    +itemKey: str
    +quantity: Decimal
    +unitPrice: Decimal
    +saleDate: date
  }
  class AnalysisResult {
    +asOf: date
    +loggedDays: int
    +requiredDays: int = 7
    +status: AnalysisStatus
    +estimates: list~BaselineEstimate~
  }
  class BaselineEstimate {
    +id: UUID
    +ownerId: UUID
    +asOfDate: date
    +itemKey: str
    +itemName: str
    +windowDays: int
    +loggedDays: int
    +averageDailyQuantity: Decimal
    +priorAverageDailyQuantity: Optional~Decimal~
    +trend: TrendDirection
    +coverDays: int
    +suggestedRestockQty: int
    +computedAt: datetime
  }
  class GuidanceResult {
    +items: list~GuidanceItem~
    +source: GuidanceSource
    +groundingPassed: bool
    +failedRule: Optional~int~
    +stockNotice: str
  }
  class GuidanceItem {
    +itemName: str
    +suggestedQty: int
    +message: str
  }
  class TrendDirection {
    <<enumeration>>
    UP
    DOWN
    STABLE
    INSUFFICIENT_DATA
  }
  class AnalysisStatus {
    <<enumeration>>
    READY
    INSUFFICIENT_HISTORY
  }
  class GuidanceSource {
    <<enumeration>>
    AI_WORDED
    TEMPLATE
  }
  AnalysisController --> BaselineAnalyzer : keeps
  AnalysisController --> GuidanceEngine : keeps
  BaselineAnalyzer --> RecordRepository : keeps
  BaselineAnalyzer ..> StructuredSalesRecord : reads
  BaselineAnalyzer ..> AnalysisResult : creates
  AnalysisResult *-- "0..*" BaselineEstimate : owns
  GuidanceEngine --> LLMService : keeps
  GuidanceEngine ..> BaselineEstimate : reads

  GuidanceEngine ..> GuidanceResult : creates
  GuidanceResult *-- "0..20" GuidanceItem : owns
  LLMService <|.. ClaudeLLMService : implements
  LLMService <|.. MockLLMService : implements
  BaselineEstimate --> TrendDirection : trend
  AnalysisResult --> AnalysisStatus : status
  GuidanceResult --> GuidanceSource : source
```

### 03-sd01-photo-extraction

Source: [`03-sd01-photo-extraction.mmd`](03-sd01-photo-extraction.mmd)

```mermaid
sequenceDiagram
  autonumber
  actor BusinessOwner
  participant ReactSPA
  participant SalesCaptureController
  participant InferenceService
  participant ObjectStorage
  participant RecordRepository
  participant VisionModel
  BusinessOwner->>ReactSPA: Choose photo of sales log and sale date
  ReactSPA->>ReactSPA: Check type (JPEG or PNG) and size (max 10 MiB)
  ReactSPA->>SalesCaptureController: POST /api/v1/extract (file, saleDate)
  SalesCaptureController->>SalesCaptureController: validateInput(saleDate, file, None)
  SalesCaptureController->>InferenceService: sanitizeImage(image)
  InferenceService-->>SalesCaptureController: sanitized JPEG (no metadata, max 2048 px)
  SalesCaptureController->>ObjectStorage: putObject(sanitizedImage, "image/jpeg")
  ObjectStorage-->>SalesCaptureController: imageKey (random UUID)
  SalesCaptureController->>RecordRepository: createSubmission(ownerId, IMAGE, saleDate, imageKey, None)
  RecordRepository-->>SalesCaptureController: submissionId (status UPLOADED)
  SalesCaptureController->>InferenceService: extractFromImage(submissionId, sanitizedImage)
  InferenceService->>RecordRepository: updateStatus(submissionId, CLASSIFYING)
  InferenceService->>VisionModel: extractFromImage(image, prompt, timeout=30)
  Note over VisionModel: Implemented by ClaudeVisionModel over HTTPS
  VisionModel-->>InferenceService: ExtractionResult (lines and confidences)
  InferenceService->>InferenceService: maskContacts(result)
  InferenceService->>InferenceService: applyConfidenceBands(submissionId, result)
  InferenceService->>RecordRepository: saveDraft(draft) sets NEEDS_VERIFICATION
  Note right of RecordRepository: Every readable draft with 1 or more lines
  InferenceService-->>SalesCaptureController: SalesDraft
  SalesCaptureController-->>ReactSPA: 200 OK ExtractResponse (draft, imageUrl)
  ReactSPA-->>BusinessOwner: Draft with "Please verify" highlights and blank LOW fields
  BusinessOwner->>ReactSPA: Check highlighted fields, fill blanks, press Confirm
  ReactSPA->>SalesCaptureController: POST /api/v1/confirm (submissionId, lines)
  SalesCaptureController->>SalesCaptureController: validateLines(lines)
  SalesCaptureController->>RecordRepository: confirmSubmission(ownerId, submissionId, lines)
  RecordRepository->>RecordRepository: Lock row, insert SALES_RECORDS, set CONFIRMED, clear draftPayload, commit
  RecordRepository-->>SalesCaptureController: ConfirmOutcome (recordIds, created=true)
  SalesCaptureController-->>ReactSPA: 201 Created (recordIds)
  ReactSPA-->>BusinessOwner: "Sales saved"
```

### 03-sd02-extraction-fallback

Source: [`03-sd02-extraction-fallback.mmd`](03-sd02-extraction-fallback.mmd)

```mermaid
sequenceDiagram
  autonumber
  actor BusinessOwner
  participant ReactSPA
  participant SalesCaptureController
  participant InferenceService
  participant RecordRepository
  participant VisionModel
  BusinessOwner->>ReactSPA: Submit photo of sales log and sale date
  ReactSPA->>SalesCaptureController: POST /api/v1/extract (file, saleDate)
  Note over SalesCaptureController,RecordRepository: Validation, sanitizeImage, putObject and createSubmission (UPLOADED) exactly as SD-01 steps 4 to 10
  SalesCaptureController->>InferenceService: extractFromImage(submissionId, sanitizedImage)
  InferenceService->>RecordRepository: updateStatus(submissionId, CLASSIFYING)
  InferenceService->>VisionModel: extractFromImage(image, prompt, timeout=30)
  Note right of VisionModel: No valid response within 30 s
  VisionModel-->>InferenceService: ProviderTimeoutError (attempt 1 of 2)
  InferenceService->>InferenceService: wait 1 s
  InferenceService->>VisionModel: extractFromImage(image, prompt, timeout=30) attempt 2
  alt Attempt 2 succeeds
    VisionModel-->>InferenceService: ExtractionResult
    Note over InferenceService,RecordRepository: maskContacts, applyConfidenceBands and saveDraft (NEEDS_VERIFICATION or REJECTED) as SD-01 steps 15 to 17
    InferenceService-->>SalesCaptureController: SalesDraft
    SalesCaptureController-->>ReactSPA: 200 OK ExtractResponse

    ReactSPA-->>BusinessOwner: Draft review continues as in SD-01
  else Attempt 2 also fails
    VisionModel-->>InferenceService: ProviderTimeoutError (attempt 2 of 2)
    InferenceService->>RecordRepository: saveDraft(draft with status AI_UNAVAILABLE, no lines)
    InferenceService-->>SalesCaptureController: SalesDraft (status AI_UNAVAILABLE)
    SalesCaptureController-->>ReactSPA: 200 OK ExtractResponse (status AI_UNAVAILABLE, manualEntry true, imageUrl)
    ReactSPA-->>BusinessOwner: "We could not read this right now" and empty form beside the photo
    BusinessOwner->>ReactSPA: Type item names, quantities and unit prices
    ReactSPA->>SalesCaptureController: POST /api/v1/confirm (submissionId, lines without draftLineId)
    SalesCaptureController->>SalesCaptureController: validateLines(lines)
    SalesCaptureController->>RecordRepository: confirmSubmission(ownerId, submissionId, lines)
    RecordRepository->>RecordRepository: Insert rows with source MANUAL, set CONFIRMED, commit
    RecordRepository-->>SalesCaptureController: ConfirmOutcome (recordIds)
    SalesCaptureController-->>ReactSPA: 201 Created (recordIds)
    ReactSPA-->>BusinessOwner: "Sales saved"
  end
```

### 03-sd03-baseline-guidance

Source: [`03-sd03-baseline-guidance.mmd`](03-sd03-baseline-guidance.mmd)

```mermaid
sequenceDiagram
  autonumber
  actor BusinessOwner
  participant ReactSPA
  participant AnalysisController
  participant BaselineAnalyzer
  participant RecordRepository
  participant GuidanceEngine
  participant LLMService
  BusinessOwner->>ReactSPA: Open the Analysis page
  ReactSPA->>AnalysisController: GET /api/v1/analysis?asOf=today
  AnalysisController->>BaselineAnalyzer: computeBaselines(ownerId, asOf)
  BaselineAnalyzer->>RecordRepository: getConfirmedRecords(ownerId, asOf - 27 days, asOf)
  RecordRepository-->>BaselineAnalyzer: StructuredSalesRecord list (CONFIRMED parents only)
  BaselineAnalyzer->>BaselineAnalyzer: buildDailyMatrix(records) logged days x items, gaps = 0
  alt Fewer than 7 logged days
    BaselineAnalyzer-->>AnalysisController: AnalysisResult (INSUFFICIENT_HISTORY, no estimates)
    AnalysisController-->>ReactSPA: 200 OK AnalysisResult
    ReactSPA-->>BusinessOwner: "Log at least 7 days of sales to see estimates (N of 7 so far)"
  else 7 or more logged days
    BaselineAnalyzer->>BaselineAnalyzer: averageOf(last 7 logged days), trend, suggestedQty
    BaselineAnalyzer->>RecordRepository: replaceBaselines(ownerId, asOf, estimates)
    BaselineAnalyzer-->>AnalysisController: AnalysisResult (READY, estimates)
    AnalysisController-->>ReactSPA: 200 OK AnalysisResult
    ReactSPA-->>BusinessOwner: Average per logged day, trend, suggested stock and stock-check notice
    BusinessOwner->>ReactSPA: Tap "Get restocking advice"
    ReactSPA->>AnalysisController: POST /api/v1/guidance (asOf)
    AnalysisController->>BaselineAnalyzer: computeBaselines(ownerId, asOf)
    BaselineAnalyzer-->>AnalysisController: AnalysisResult (READY, same estimates)
    AnalysisController->>GuidanceEngine: generateGuidance(estimates)
    GuidanceEngine->>GuidanceEngine: buildPrompt(top 20 items by average)
    GuidanceEngine->>LLMService: generateJson(prompt, schema, timeout=15)
    alt Response within 15 s
      LLMService-->>GuidanceEngine: JSON text
      GuidanceEngine->>GuidanceEngine: checkGrounding(raw, estimates) rules 1 to 7
      alt All rules pass
        GuidanceEngine-->>AnalysisController: GuidanceResult (AI_WORDED)
      else Any rule fails
        GuidanceEngine->>GuidanceEngine: fallbackTemplate(estimates)
        GuidanceEngine-->>AnalysisController: GuidanceResult (TEMPLATE, failedRule)
      end
    else Timeout or provider error, no retry
      GuidanceEngine->>GuidanceEngine: fallbackTemplate(estimates)
      GuidanceEngine-->>AnalysisController: GuidanceResult (TEMPLATE)
    end
    AnalysisController-->>ReactSPA: 200 OK GuidanceResult
    ReactSPA-->>BusinessOwner: Advice per item, label "Standard advice" when TEMPLATE, stock-check notice
  end
```

### 03-sd04-text-and-manual-entry

Source: [`03-sd04-text-and-manual-entry.mmd`](03-sd04-text-and-manual-entry.mmd)

```mermaid
sequenceDiagram
  autonumber
  actor BusinessOwner
  participant ReactSPA
  participant SalesCaptureController
  participant InferenceService

  participant RecordRepository
  participant VisionModel
  alt Typed or pasted sales text
    BusinessOwner->>ReactSPA: Paste sales text and choose sale date
    ReactSPA->>SalesCaptureController: POST /api/v1/extract (text, saleDate)
    SalesCaptureController->>SalesCaptureController: validateInput(saleDate, None, text)
    SalesCaptureController->>InferenceService: maskText(text)
    InferenceService-->>SalesCaptureController: maskedText (phones and emails replaced)
    SalesCaptureController->>RecordRepository: createSubmission(ownerId, TEXT, saleDate, None, maskedText)
    RecordRepository-->>SalesCaptureController: submissionId (UPLOADED)
    SalesCaptureController->>InferenceService: extractFromText(submissionId, maskedText)
    InferenceService->>RecordRepository: updateStatus(submissionId, CLASSIFYING)
    InferenceService->>VisionModel: extractFromText(maskedText, prompt, timeout=30)
    VisionModel-->>InferenceService: ExtractionResult
    Note over InferenceService,RecordRepository: maskContacts, applyConfidenceBands, saveDraft. Retry and AI_UNAVAILABLE exactly as SD-02
    InferenceService-->>SalesCaptureController: SalesDraft
    SalesCaptureController-->>ReactSPA: 200 OK ExtractResponse
    ReactSPA-->>BusinessOwner: Draft review and confirm exactly as SD-01 steps 20 to 28
  else Standalone manual entry (no AI)
    BusinessOwner->>ReactSPA: Open "Enter manually", choose date, type lines
    ReactSPA->>ReactSPA: Generate submissionId (UUID v4) once per form
    ReactSPA->>SalesCaptureController: POST /api/v1/manual-entries (submissionId, saleDate, lines)
    SalesCaptureController->>SalesCaptureController: validateInput(saleDate, None, None) and validateLines(lines)
    SalesCaptureController->>RecordRepository: createManualSubmission(ownerId, submissionId, saleDate, lines)
    RecordRepository->>RecordRepository: Insert SUBMISSIONS (MANUAL, CONFIRMED) and SALES_RECORDS (MANUAL) in one transaction
    RecordRepository-->>SalesCaptureController: ConfirmOutcome (recordIds, created)
    SalesCaptureController-->>ReactSPA: 201 Created, or 200 OK when submissionId already exists
    ReactSPA-->>BusinessOwner: "Sales saved"
  end
```

### 04-submission-state

Source: [`04-submission-state.mmd`](04-submission-state.mmd)

```mermaid
stateDiagram-v2
  direction TB
  state "UPLOADED" as U
  state "CLASSIFYING" as C
  state "CONFIRMED" as F
  state "Open submission (not in sales history)" as OPEN {
    state "NEEDS_VERIFICATION" as V
    state "AI_UNAVAILABLE" as A
    state "REJECTED" as R
  }

  [*] --> U : Valid photo or text / create submission
  [*] --> F : Standalone manual entry [valid lines]
  U --> C : Extraction starts
  C --> C : Retryable failure [attempt 1] / retry once
  C --> V : Readable result with 1+ lines / save draft
  C --> R : Overall confidence below 0.40, or no lines
  C --> A : Attempt 2 fails, or non-retryable error
  U --> A : Stale over 120 s
  C --> A : Stale over 120 s
  OPEN --> OPEN : Confirm fails [invalid lines or DB error] / rollback, status unchanged
  OPEN --> F : Confirm [valid lines] / insert rows + set CONFIRMED in one transaction
  F --> F : Repeat confirm / return existing record IDs
  OPEN --> [*] : Owner discards, or 30-day purge
  F --> [*] : Owner deletes submission
```

### 05-erd

Source: [`05-erd.mmd`](05-erd.mmd)

```mermaid
erDiagram
  OWNERS ||--o{ SUBMISSIONS : owns OWNERS ||--o{ BASELINE_ESTIMATES : receives SUBMISSIONS ||--o{ SALES_RECORDS : contains  OWNERS {
    uuid id PK
    varchar displayName "max 100"
    timestamptz createdAt
  }
  SUBMISSIONS {
    uuid id PK
    uuid ownerId FK "ON DELETE CASCADE"
    varchar inputType "IMAGE, TEXT or MANUAL"

    varchar status "SubmissionStatus, CHECK"
    date saleDate "not in future"
    varchar imageKey "nullable, IMAGE only"
    text maskedText "nullable, TEXT only"
    jsonb draftPayload "nullable, cleared on confirm"
    timestamptz createdAt
    timestamptz updatedAt
    timestamptz confirmedAt "nullable"
  }
  SALES_RECORDS {
    uuid id PK
    uuid submissionId FK "ON DELETE CASCADE"
    int lineNo "1 to 100, UNIQUE with submissionId"
    varchar itemName "1 to 100 chars, trimmed"
    varchar itemKey "casefolded itemName"
    numeric quantity "NUMERIC(10,2), above 0"
    numeric unitPrice "NUMERIC(12,2), 0 or more"
    varchar source "AI_EXTRACTED or MANUAL"
  }
  BASELINE_ESTIMATES {
    uuid id PK
    uuid ownerId FK "ON DELETE CASCADE"
    date asOfDate "UNIQUE with ownerId, itemKey"
    varchar itemKey
    varchar itemName "latest spelling"
    int windowDays "7"
    int loggedDays
    numeric averageDailyQuantity "NUMERIC(12,4)"
    numeric priorAverageDailyQuantity "nullable"
    varchar trend "TrendDirection, CHECK"
    int coverDays "7"
    int suggestedRestockQty "0 or more"
    timestamptz computedAt
  }
```

### 06-deployment

Source: [`06-deployment.mmd`](06-deployment.mmd)

```mermaid
flowchart TB
  DEV["Developer laptop<br/>docker compose: frontend, backend,<br/>postgres:16, minio (local S3)<br/>AI_PROVIDER=mock by default"]
  subgraph GITHUB["GitHub"]
    GH["Repository<br/>main branch protected"]
    subgraph CI["GitHub Actions workflow ci.yml"]
      S1["1 Backend: ruff + pytest<br/>Postgres service container, mocks"]
      S2["2 Frontend: npm ci, lint,<br/>vitest, vite build"]
      S3["3 docker build backend and frontend"]
      S4["4 Deploy (push to main only):<br/>POST Render deploy hooks"]
    end
    GSEC[/"GitHub secrets:<br/>RENDER_DEPLOY_HOOK_BACKEND<br/>RENDER_DEPLOY_HOOK_FRONTEND"/]
  end
  subgraph RENDER["Render - production"]
    FE["Web Service: frontend<br/>Docker, nginx serves SPA<br/>config.js from API_BASE_URL"]
    BE["Web Service: backend<br/>Docker, uvicorn on PORT<br/>alembic upgrade head at start"]
    DB[("Render PostgreSQL 16")]
    RSEC[/"Render env vars and secrets<br/>DATABASE_URL, AI_API_KEY, APP_ACCESS_CODE,<br/>S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY"/]
  end
  R2[("Cloudflare R2 bucket<br/>private, signed URLs")]
  AI["Anthropic Claude API"]
  USER["Business Owner browser"]
  DEV -->|"git push / pull request"| GH
  GH --> S1 --> S2 --> S3 --> S4
  GSEC -.-> S4
  S4 -->|"HTTPS deploy hook<br/>Render builds from Dockerfile"| RENDER
  RSEC -.-> BE
  USER -->|"HTTPS"| FE
  USER -->|"HTTPS REST"| BE
  BE -->|"SQL over TLS"| DB
  BE -->|"HTTPS S3 API"| R2
  BE -->|"HTTPS"| AI
```

### 07-ai-pipeline

Source: [`07-ai-pipeline.mmd`](07-ai-pipeline.mmd)

```mermaid
flowchart TB
  START(["Owner submits photo or text<br/>POST /api/v1/extract"]) --> TYPE{"Exactly one of<br/>photo or text?"}
  TYPE -->|"No"| ERR400["400 validation error<br/>no submission, no AI call"]
  TYPE -->|"Photo"| VIMG{"JPEG or PNG by magic bytes,<br/>max 10 MiB, valid sale date?"}

  TYPE -->|"Text"| VTXT{"1 to 5000 chars,<br/>valid sale date?"}
  VIMG -->|"No"| ERR400
  VTXT -->|"No"| ERR400
  VIMG -->|"Yes"| EXIF["sanitizeImage: apply orientation,<br/>re-encode pixels only as JPEG q85,<br/>long edge max 2048 px"]
  EXIF --> STORE["putObject to private R2 bucket<br/>key = random UUID"]
  STORE -->|"Storage error"| ERR503["503, no submission,<br/>no AI call"]
  STORE --> UP["createSubmission<br/>status = UPLOADED"]
  VTXT -->|"Yes"| MASK["maskText: phones and emails<br/>to [removed]"]
  MASK --> UP
  UP --> CLS["status = CLASSIFYING"]
  CLS --> CALL["VisionModel call to Claude<br/>timeout 30 s, forced tool output"]
  CALL --> OK{"Schema-valid<br/>response?"}
  OK -->|"Timeout, 429, 5xx, network<br/>or invalid schema: attempt 1"| RETRY["Wait 1 s, attempt 2"]
  RETRY --> CALL
  OK -->|"Same failure on attempt 2,<br/>or 400, 401, 403"| UNAV["status = AI_UNAVAILABLE"]
  OK -->|"Yes"| POST["maskContacts on returned text"]
  POST --> READ{"overallConfidence<br/>at least 0.40?"}
  READ -->|"No"| REJ["status = REJECTED<br/>retake guidance"]
  READ -->|"Yes"| EMPTY{"At least one<br/>sales line?"}
  EMPTY -->|"No"| REJ2["status = REJECTED<br/>'No sales lines found'"]
  EMPTY -->|"Yes"| BAND{"Band per field"}
  BAND -->|"0.85 or higher"| HIGH["HIGH: prefill, no warning"]
  BAND -->|"0.60 up to 0.85"| MED["MEDIUM: prefill,<br/>'Please verify'"]
  BAND -->|"Confidence missing"| UNS["UNSCORED: prefill,<br/>'Please verify'"]
  BAND -->|"Below 0.60 or value missing"| LOW["LOW: blank,<br/>'Could not read - enter value'"]
  HIGH --> REVIEW["status = NEEDS_VERIFICATION<br/>draft saved in draftPayload"]
  MED --> REVIEW
  UNS --> REVIEW
  LOW --> REVIEW
  REVIEW --> GATE{"All MEDIUM and UNSCORED<br/>fields ticked, all LOW filled?"}
  GATE -->|"No"| REVIEW
  UNAV --> MANUAL["Manual entry form<br/>next to photo or text"]
  REJ -->|"Enter manually"| MANUAL
  REJ2 -->|"Enter manually"| MANUAL
  REJ -->|"Retake photo"| START
  GATE -->|"Yes"| CONFIRM["POST /api/v1/confirm<br/>server-side validation"]
  MANUAL --> CONFIRM
  CONFIRM --> DONE(["One transaction:<br/>SALES_RECORDS rows + status CONFIRMED"])

  classDef privacy fill:#E8F1FB,stroke:#2F6DB5,color:#1B3A5C
  classDef fail fill:#FCEBEA,stroke:#B5402F,color:#5C1E16
  classDef ok fill:#E9F5EC,stroke:#2F8A4C,color:#173F24
  class EXIF,MASK,STORE,POST privacy
  class ERR400,ERR503,UNAV,REJ,REJ2,LOW fail
  class HIGH,DONE ok
```
