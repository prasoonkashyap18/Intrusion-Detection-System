# MVP Scope: AI-Based Network Intrusion Detection System (AI-IDS)

## 1. Project Name
AI-Based Network Intrusion Detection System (AI-IDS)

## 2. Project Objective
Build a web-based AI-powered Network Intrusion Detection System that allows a user to upload network-flow data, process it through an ML-based detection pipeline, classify traffic, generate confidence and severity information, store detection results, and visualize them through a security dashboard.

## 3. MVP Core Workflow

```
User
  ↓
React Web Application
  ↓
CSV Upload
  ↓
FastAPI Backend
  ↓
Data Validation
  ↓
Preprocessing
  ↓
ML Model
  ↓
Intrusion Detection
  ↓
Confidence
  ↓
Severity
  ↓
SQLite Database
  ↓
Security Dashboard
```

## 4. MVP Features
- Web-based interface
- CSV network-flow upload
- Dataset validation
- Data preprocessing
- ML-based intrusion detection
- Attack classification
- Prediction confidence
- Detection severity
- Detection result storage
- Detection history
- Security dashboard
- Search
- Filtering
- Analytics
- Model performance metrics

## 5. Planned Technology Stack

**Frontend**
- React
- TypeScript
- Tailwind CSS

**Backend**
- Python
- FastAPI

**Machine Learning**
- Python
- Pandas
- NumPy
- scikit-learn
- Joblib

**Database**
- SQLite

## 6. Features Explicitly Excluded from the MVP
- Live network traffic capture
- PCAP processing
- Real-time packet capture
- Threat intelligence
- Event correlation
- Incident management
- Asset inventory
- LLM security analyst
- Automated response
- Kubernetes
- Unnecessary microservices
- Enterprise authentication

These features may be implemented later as part of the substantial version of the project.

## 7. Definition of Done
The MVP is complete when:
- The application runs locally.
- A user can open the application in a browser.
- A user can upload an appropriate network-flow CSV.
- The backend validates the uploaded data.
- The data is processed through the ML pipeline.
- The ML model generates predictions.
- Confidence is generated appropriately.
- Severity is calculated using a transparent MVP rule.
- Results are stored.
- Results appear in the web interface.
- Dashboard statistics are based on actual detection results.
- Model performance metrics are based on actual evaluation.
- The complete workflow can be demonstrated.
- Documentation exists.
- The project can be run from a clean environment.

**Important constraints:**
- Do not fabricate ML metrics.
- Do not claim a model is accurate until it has actually been evaluated.
- Do not invent dataset results.
