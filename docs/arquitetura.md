                     API QUALITY ORCHESTRATOR
                              │
                              ▼
                    ┌─────────────────────┐
                    │ Geração de testes   │
                    │ Playwright API      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Execução             │
                    │ pytest + Playwright  │
                    └──────────┬──────────┘
                               │
                               │
              ┌────────────────┼─────────────────┐
              │                │                 │
              ▼                ▼                 ▼
         JUnit XML          HTTP NDJSON      Assertions NDJSON
              │                │                 │
              ▼                ▼                 ▼
       test_failures     http_transactions  assertion_results
              │                │                 │
              │                │                 │
              │          ┌─────┴─────┐           │
              │          │           │           │
              │       Request      Response      │
              │          │           │           │
              │          ├── headers │           │
              │          ├── query   │           │
              │          └── body    │           │
              │                      │           │
              │                      │           │
              └──────────────┬───────┴───────────┘
                             │
                             │
              ┌──────────────▼────────────────┐
              │       ExecutionResult         │
              │                               │
              │  mesmo objeto de domínio      │
              │  com múltiplos campos irmãos  │
              └──────────────┬────────────────┘
                             │
              ┌──────────────┼─────────────────────────┐
              │              │                         │
              ▼              ▼                         ▼
       trace_artifacts  evidence_failures        skipped_tests
              │              │                         │
              │              │                         │
              │              │                    JUnit XML
              │              │                    (contador)
              │              │
              │        consequência de
              │        falha na evidência
              │        de Trace
              │
              ▼
      ┌───────────────────────┐
      │ Masking estrutural    │
      │ do Trace              │
      │                       │
      │ headers/cookies       │
      │ por nome de campo     │
      └───────────┬───────────┘
                  │
                  ▼
      ┌───────────────────────┐
      │ known_secret_values   │
      │                       │
      │ segunda camada        │
      │ de proteção do Trace  │
      └───────────────────────┘


              MASKING TRANSVERSAL
        ─────────────────────────────────────
        Aplicado pelo PlaywrightAdapter
        antes da persistência:

        • stdout
        • stderr
        • test_failures.error_message
        • HTTP headers
        • HTTP query parameters
        • HTTP body
        • evidence_failures.message
        • demais dados textuais aplicáveis
        ─────────────────────────────────────


                       ↓
              ┌──────────────────────┐
              │ PersistExecutionResult│
              │ UseCase               │
              └──────────┬───────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │ result.json  │
                  │ schema 1.8   │
                  └──────┬───────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ JsonExecutionResult  │
              │ Reader               │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ ReportEngine         │
              │                      │
              │ correlação por       │
              │ test_id              │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ HTML Renderer        │
              │                      │
              │ report.html          │
              └──────────┬───────────┘
                         │
                         ▼
                       ┌─────┐
                       │ QA  │
                       │audit│
                       └─────┘