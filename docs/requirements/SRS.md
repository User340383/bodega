# Software Requirements Specification — diagrams (Milestone 2, section 6)

Milestone 2 section 6: "The same source is committed to the project repository in the GitHub
Markdown file docs/requirements/SRS.md, where GitHub renders it natively." This file holds that
source. It was recovered from `Milestone_2_final.docx` (version 2.0, baselined), with the
document's line breaks kept. The requirement text itself stays in the baselined document. Where
Milestone 3 section 3 changes a requirement, see `../TRACEABILITY.md`.

Every diagram renders with Mermaid CLI 11.4.2; CI checks this on every push.

### 01-use-case

Source: [`01-use-case.mmd`](01-use-case.mmd)

```mermaid
flowchart LR
    Owner(["👤 Business Owner<br/>(UR-01)"])
    AI(["⚙ External AI Service<br/>(EXT-01)"])

    subgraph SYS["AI Forecasting and Organizational Tool for Informal Businesses"]
        UC1(("UC1<br/>Submit sales-log image"))
        UC2(("UC2<br/>Submit sales text"))
        UC3(("UC3<br/>Enter sales manually"))
        UC4(("UC4<br/>Review and confirm<br/>sales draft"))
        UC5(("UC5<br/>View sales history"))
        UC6(("UC6<br/>Delete stored<br/>sales data"))
        UC7(("UC7<br/>View trend analysis"))
        UC8(("UC8<br/>View restocking<br/>guidance"))
        UC9(("UC9<br/>View results<br/>dashboard"))
        UC10(("UC10<br/>Extract structured<br/>sales data"))
        UC11(("UC11<br/>Generate guidance text"))
    end

    Owner --- UC1
    Owner --- UC2
    Owner --- UC3
    Owner --- UC4
    Owner --- UC5
    Owner --- UC6
    Owner --- UC7
    Owner --- UC8
    Owner --- UC9

    UC1 -. "«include»" .-> UC10
    UC2 -. "«include»" .-> UC10
    UC10 -. "«include»" .-> UC4
    UC3 -. "«extend» AI unavailable<br/>or unreadable input" .-> UC4
    UC8 -. "«include»" .-> UC11
    UC10 --- AI
    UC11 --- AI
```

### 02-user-journey

Source: [`02-user-journey.mmd`](02-user-journey.mmd)

```mermaid
flowchart TD
    A([Owner selects sales date and<br/>uploads image or pastes text]) --> B{Input valid?<br/>FR-101 to FR-103}
    B -- No --> B1[Show validation message;<br/>no AI request sent] --> A
    B -- Yes --> P[Apply privacy safeguards:<br/>mask contacts, strip image metadata<br/>FR-212, FR-213]
    P --> C[Send to External AI Service<br/>FR-201]
    C --> T{Response within 30 s?<br/>FR-211}
    T -- No --> R{Automatic retry<br/>already used?}
    R -- No --> C
    R -- Yes --> M[Show AI temporarily unavailable;<br/>keep date and input;<br/>offer manual entry<br/>FR-104, FR-211]
    T -- "Yes: error, unreadable<br/>response or no rows" --> F[Show failure or<br/>no-records message<br/>FR-205] --> M
    T -- "Yes: rows returned" --> L{Overall legibility<br/>score below 0.40?<br/>FR-210}
    L -- Yes --> U[Image unreadable:<br/>show retake tips<br/>FR-210] --> M
    L -- No --> S[Read confidence score<br/>of each field<br/>FR-206]
    S --> H{Field confidence}
    H -- "≥ 0.85 high" --> H1[Prefill value,<br/>no warning<br/>FR-207]
    H -- "0.60 to 0.84 medium,<br/>or score missing" --> H2[Prefill and highlight<br/>Please verify<br/>FR-208]
    H -- "below 0.60 low" --> H3[Leave blank:<br/>Could not read, enter value<br/>FR-209]
    H1 --> D[Owner reviews draft<br/>FR-202, FR-203]
    H2 --> D
    H3 --> D
    M --> D
    D --> V{All rows valid and every<br/>highlighted field verified?<br/>FR-204, FR-208}
    V -- No --> D
    V -- Yes --> W[Owner confirms draft]
    W --> X{Save succeeded?<br/>FR-302}
    X -- No --> X1[Show save-failure message;<br/>no rows persisted] --> D
    X -- Yes --> Y([Records added to sales history;<br/>raw input deleted within 24 h<br/>FR-301, FR-215])
```

### 03-ai-fallback

Source: [`03-ai-fallback.mmd`](03-ai-fallback.mmd)

```mermaid
flowchart TD
    A([Owner opens results, analysis,<br/>or guidance view]) --> B{Any confirmed records?}
    B -- No --> B1[Show no-sales-data message<br/>and link to input form<br/>FR-602]
    B -- Yes --> C[Recalculate daily quantities<br/>and baseline estimates<br/>FR-401, FR-404]
    C --> D{Item has 2 or more<br/>distinct recorded dates?}
    D -- No --> D1[Show Insufficient history<br/>for that item<br/>FR-403]
    D -- Yes --> E[Show estimate rounded<br/>to 2 decimals<br/>FR-402, FR-601]
    E --> G{At least one<br/>eligible item?}
    D1 --> G
    G -- No --> G1[Show Insufficient history;<br/>no guidance request<br/>FR-503]
    G -- Yes --> H[Send only calculated figures<br/>to External AI Service<br/>FR-501]
    H --> I{Non-empty response<br/>within 15 s?<br/>FR-505}
    I -- No --> I1[Show Guidance unavailable;<br/>figures stay visible<br/>FR-503, FR-505]
    I -- Yes --> J{Every item and number in<br/>the text matches the<br/>calculated figures?<br/>FR-504}
    J -- No --> J1[Withhold guidance;<br/>show figures only<br/>FR-504]
    J -- Yes --> K([Display guidance with figures<br/>and stock-check notice<br/>FR-502])
```
