# Architecture

Seldon runs as a local web application. The React interface sends a research question to a FastAPI backend, which coordinates the research and stores the resulting report in SQLite. The backend makes the external requests.

```mermaid
flowchart LR
  U[User] --> UI[React interface]
  UI -->|Local API| API[FastAPI]
  API --> DB[(SQLite)]
  API --> A[Research workflow]
  A --> N[Nebius Token Factory]
  A --> T[Tavily]
  A --> S[SEC EDGAR]
  S --> F[Financial evidence and calculations]
  T --> E[News and search sources]
  F --> R[Report]
  E --> R
  N --> R
  R --> API
```

Nebius helps plan the research, analyze information and review the investment view. Tavily searches the web. SEC EDGAR supplies company filings and structured financial facts. Each serves a different role in the report.

## From question to report

```mermaid
flowchart TD
  Q[Question or selected reports] --> P[Research plan]
  P --> I[Identify companies]
  I --> D[Search and retrieve financial data]
  D --> V[Check financial facts and periods]
  V --> F[Targeted follow-up when needed]
  F --> X[News and investment analysis]
  X --> W[Review investment points]
  W --> R[Save report and provider activity]
  R --> UI[Show the answer and sources]
```

The workflow matches financial observations to their issuers, reporting periods, units and source records. It calculates measures such as free cash flow when the inputs are comparable. News and financial evidence are then available to investment synthesis. The review records findings at the level of individual points.

Comparison research uses saved reports as context and prioritizes the comparative answer before the news stage. Provider activity records calls made during the task. The interface displays the report, financial records and links to their sources.

The main implementation is in `backend/agent/`, with provider adapters in `backend/providers/`, evidence and financial processing in `backend/evidence/` and `backend/financial/`, and the interface in `frontend/src/`. The API and report contracts are in `contracts/`.