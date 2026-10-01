# AI-Based Network Intrusion Detection System (AI-IDS)

A web-based, AI-powered Network Intrusion Detection System. Users upload network-flow data, which is processed through a machine-learning detection pipeline to classify traffic, produce confidence and severity information, and present results through a security dashboard.

## Current Status

MVP development — in progress. No application code, ML model, or dataset has been added yet. See [MVP_SCOPE.md](MVP_SCOPE.md) for the full scope definition.

## Main Technology Stack

**Frontend:** React, TypeScript, Tailwind CSS
**Backend:** Python, FastAPI
**Machine Learning:** Python, Pandas, NumPy, scikit-learn, Joblib
**Database:** SQLite

## High-Level Workflow

```
User → React Web Application → CSV Upload → FastAPI Backend
     → Data Validation → Preprocessing → ML Model
     → Intrusion Detection → Confidence → Severity
     → SQLite Database → Security Dashboard
```

## Planned Future Direction

Beyond the MVP, this project may be extended toward a more complete cybersecurity platform, potentially including live network traffic capture, PCAP processing, threat intelligence, event correlation, incident management, automated response, and other capabilities. These are explicitly out of scope for the current MVP and are not yet designed or committed to.
