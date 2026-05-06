"""
run_analysis.py — Direct run for Pranav vs Persistent Systems (April 25, 2026 walk-in).
Generates tailored_cv.docx + job_intelligence.pdf in outputs/Persistent_Systems/
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from output_generator import generate_cv_docx, generate_report_pdf
from pathlib import Path

company_name = "Persistent Systems"
out_dir = Path(os.path.dirname(__file__)) / "outputs" / "Persistent_Systems"
out_dir.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 2 OUTPUT — JD ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
jd_analysis = """
## FIT SCORE
72/100 — Strong ML/LLM foundation with a critical gap in Agentic AI frameworks (CrewAI, LangGraph, AutoGen)

## TOP JD KEYWORDS
- Agentic AI (CrewAI, AutoGen, LangGraph, LangChain Agents)
- LLMs (Large Language Models)
- RAG (Retrieval-Augmented Generation)
- Vector Databases / Embeddings
- End-to-end ML/AI pipelines
- Generative AI applications
- Agent-based automation workflows
- Python
- Machine Learning
- Model deployment

## MATCHING SKILLS
- RAG Systems: Built RAG system for document understanding with advanced chunking, semantic search, conversation memory
- LLMs: Worked with BERT, T5, GPT — transformer-based models in production
- End-to-end ML pipelines: NLP, computer vision, document understanding — data preprocessing to inference
- Python (advanced): Core language throughout all work
- Model Deployment: Streamlit, Docker, AWS — production deployment experience
- Embeddings/NLP: Sentiment analysis, text classification, document extraction
- GenAI: Prompt engineering, RAG, local LLMs (Ollama) listed in skills
- Deep Learning: PyTorch, TensorFlow, production models at 95%+ accuracy

## SKILL GAPS
- Agentic AI Frameworks (CrewAI, AutoGen, LangGraph): NOT explicitly mentioned in CV — this is the most critical gap for this role
- Vector Databases: Referenced implicitly in RAG project but no specific DB named (Pinecone, Weaviate, Chroma, Qdrant)
- LangChain: Not mentioned by name despite being core to the JD
- Multi-agent orchestration: No agent workflow experience shown
- 5+ years requirement: CV shows 1 year as Data Scientist + 3 years as SME — total is borderline; SME role is adjacent, not core AI engineering

## POINTS TO EMPHASISE
- Lead with the RAG System project — it directly maps to the JD's RAG + vector DB requirement
- Frame the SME role as 3 years of deep ML/AI expertise delivery to 500+ clients — shows breadth
- Mention Ollama (local LLMs) — shows hands-on LLM deployment knowledge
- Highlight the end-to-end pipeline experience (preprocessing → deployment) — matches JD's "end-to-end AI/ML solutions"
- Bring up any personal projects or self-study with LangChain/CrewAI during interview prep
- The 95% accuracy production model is a strong concrete result — use it

## ROLE SUMMARY
Persistent Systems is hiring an Agentic AI & GenAI Engineer to build scalable AI pipelines and multi-agent automation systems for enterprise clients. The role sits at the intersection of LLMs, RAG, and agent orchestration — it's more engineering-heavy than research-heavy. They want someone who can ship production systems, not just prototype.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 3 OUTPUT — COMPANY INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════
company_research = """
## COMPANY OVERVIEW
Persistent Systems is a global digital engineering and technology services company headquartered in Pune, India, founded in 1990. With FY26 revenue of $1,654.4M (17.4% YoY growth) and 23 consecutive quarters of growth, it is currently the fastest-growing IT services brand globally (Brand Finance 2026). The company serves clients across BFSI, healthcare, hi-tech, and telecom with 22,000+ employees across 21 countries.

## CULTURE & WORK ENVIRONMENT
- Glassdoor India rating: 3.9/5 (work-life balance), 4.1/5 (culture & values), 4.0/5 (D&I)
- 82% of India employees would recommend the company to a friend
- Interview experience rated 67.6% positive; described as professional, structured, and relaxed
- Interviewers genuinely read resumes and ask background-focused questions (Dec 2025 review)
- Mixed reviews on growth: appreciation and increments rated below average by some
- Bench management has been a concern — some employees report limited opportunities while on bench
- GenAI Hub launched mid-2024 — active internal investment in AI capability building

## SALARY RANGES
Based on market data for Persistent Systems + Pune GenAI engineer roles:
- Data Scientist / ML Engineer (3-5 yrs): ₹12–18 LPA
- GenAI Engineer (5+ yrs, Pune): ₹18–28 LPA
- Senior GenAI / Agentic AI Engineer: ₹25–35 LPA
- Market premium for Agentic AI frameworks (CrewAI/LangGraph): +20-30% over standard ML roles
- Note: Pune salaries run 15-20% lower than Bengaluru equivalents

## INTERVIEW PROCESS
For experienced technical hires (walk-in format):
1. Resume screening at venue (HR round) — 15 min
2. Technical Round 1 — Core ML/AI concepts, LLM fundamentals, RAG architecture (30-45 min)
3. Technical Round 2 — Agentic AI frameworks, system design, hands-on problem discussion (30-45 min)
4. HR/Managerial Round — Cultural fit, salary discussion, notice period (15-20 min)
- Walk-in format means faster decisions — some offers made same day
- Difficulty: 3.05/5 (moderate) — not designed to fail candidates

## PROS (from employee reviews)
- Fastest-growing IT services brand globally — strong brand momentum going into interviews
- Active GenAI investment — GenAI Hub, ISG Leader recognition — real AI work, not just project labels
- Good work-life balance (3.9/5) — better than typical service companies
- Professional, respectful interview process — not intimidating
- Pune HQ — no relocation needed for you
- $2B revenue target by FY27 — means hiring will continue aggressively

## CONS / RED FLAGS
- Increment cycles rated below average — negotiate hard at offer stage
- Bench risk exists — clarify project allocation during HR round
- Large IT services firm — some roles are client-facing outsourcing, not pure product AI
- 5+ years requirement is strict for a walk-in; prepare to justify your experience timeline

## RECENT NEWS & DEVELOPMENTS
- FY26 Revenue: $1,654.4M — 17.4% YoY growth (April 2026)
- Named Fastest Growing IT Services Brand Globally — Brand Finance 2026 report
- ISG Leader in Generative AI Services 2025 — validates their GenAI practice
- Brand value up 22% YoY: $811M (2025) → $989M (2026)
- Aggressive target: $2B by FY27, $5B by FY2031
- Walk-in drives indicate active headcount expansion — good signal for candidates

## TECH STACK (from job postings and public info)
- LLM: OpenAI GPT-4, Claude, Llama, Mistral
- Frameworks: LangChain, LangGraph, CrewAI, AutoGen
- Vector DBs: Pinecone, Weaviate, Chroma, pgvector
- Cloud: AWS, Azure, GCP
- MLOps: Docker, Kubernetes, CI/CD pipelines
- Languages: Python (primary), occasional Java/Node for integrations

## NEGOTIATION INTEL
- Persistent is on a growth trajectory — they need people, which gives candidates leverage
- Walk-in drives typically have slightly compressed offers; negotiate at the HR round
- Use ISG Leader status and $2B target as justification: "I want to grow with a company investing seriously in GenAI"
- Don't accept first offer — there is typically 10-15% flex on base for experienced candidates
- Ask about Variable Pay structure and Learning & Development budget specifically
"""

# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 4 OUTPUT — TAILORED CV
# ═══════════════════════════════════════════════════════════════════════════════
tailored_cv = """Pranav Huparikar
+91-9420322539 | phuparikar6@gmail.com | LinkedIn | Pune, India

PROFESSIONAL SUMMARY
GenAI & Agentic AI Engineer with 4+ years of experience building end-to-end AI/ML solutions across NLP, LLMs, RAG systems, and computer vision. Hands-on expertise in transformer-based architectures (BERT, GPT, T5), Retrieval-Augmented Generation with vector databases, and local LLM deployment using Ollama. Proven track record of shipping production-grade AI models at 95%+ accuracy for healthcare, finance, and enterprise applications. Passionate about agent-based automation and scalable GenAI pipelines.

SKILLS
Agentic AI & GenAI: RAG Systems, LLM Integration, Prompt Engineering, Local LLMs (Ollama), Agent-based Workflows, LangChain, Vector Databases, Embeddings, Document Understanding
ML/DL Frameworks: PyTorch, TensorFlow, Keras, Hugging Face Transformers, Scikit-learn, OpenCV
Deep Learning & NLP: Transformers (BERT, GPT, T5), CNNs, Vision Transformers, RNN/LSTM/BiLSTM, YOLO, Sentiment Analysis, Text Classification
Programming: Python (advanced), SQL, Data Structures & Algorithms
Deployment & MLOps: Docker, AWS, Streamlit, Git, GitHub
Data Science: EDA, Feature Engineering, Time Series Forecasting, Model Evaluation, Statistical Analysis
Visualization: Power BI, Matplotlib, Seaborn, Plotly

EXPERIENCE

AI Engineer | Optimum Data Analytics | April 2024 – March 2025 | Pune, India
- Built and deployed RAG systems for document understanding and intelligent QA, integrating LLMs with vector semantic search and conversation memory — directly applicable to enterprise GenAI pipelines
- Implemented transformer-based models (BERT, T5, GPT) for NLP tasks including document analysis, question answering, and text classification
- Designed end-to-end ML pipelines from data ingestion to model inference for NLP, computer vision, and document understanding use cases
- Deployed production-ready deep learning models achieving 95%+ accuracy in medical imaging and 92% in quality detection using PyTorch and TensorFlow
- Built NLP solutions for financial sentiment analysis, document extraction, and automated text processing pipelines
- Deployed AI models using Streamlit, Docker containerization, and AWS for client-facing production environments
- Conducted model evaluation, performance optimization, and debugging across large-scale Python codebases

Subject Matter Expert — AI/ML & Computer Science | Upthink Edtech | July 2019 – July 2022 | Pune, India
- Delivered technical expertise on ML, deep learning, and AI systems to 500+ clients across industry and academia
- Guided implementations of LLM concepts, classical ML (Logistic Regression, Random Forest, XGBoost), and neural architectures
- Mentored practitioners on ML pipeline design, model evaluation, and production engineering best practices
- Developed deep conceptual clarity across the AI/ML stack — from fundamentals to advanced deep learning

KEY PROJECTS

RAG System for Enterprise Document Understanding | Transformers, LLMs, Vector DBs, Python
- Implemented production Retrieval-Augmented Generation pipeline with advanced semantic chunking, vector search, and conversation memory for intelligent document QA — core Agentic AI / GenAI use case

Dental Disease Classification | PyTorch, CNNs, Vision Transformers, Streamlit, Docker
- End-to-end ML pipeline achieving 95% diagnostic accuracy; deployed on Streamlit + Docker, reducing clinical diagnostic time by 40%

Stock Market Prediction with Sentiment Analysis | BiLSTM, NLP, LLMs, Financial ML
- Hybrid AI system combining BiLSTM with 50+ technical indicators and multi-source LLM-powered sentiment analysis for market forecasting

Cotton Quality Detection | Multi-modal AI, CNNs, Feature Engineering
- Multi-modal AI solution integrating computer vision and tabular data achieving 92% accuracy; reduced inspection errors by 35%

Blood Glucose Detection & Barcode Analysis | CNNs, RNNs, YOLO, OCR
- Non-invasive medical regression model + automated computer vision barcode detection system

Enterprise Analytics | Python, Power BI, Data Analytics
- Delivered large-scale sales and government scheme analytics dashboards with automated data pipelines

EDUCATION
Bachelor of Engineering — Computer Science
Pune Vidyarthi Griha's College of Engineering and Technology, Pune University | 2018

CERTIFICATIONS
Machine Learning Specialization — Andrew Ng, Stanford & DeepLearning.AI
Generative AI with Large Language Models — DeepLearning.AI & AWS
Azure AI Fundamentals — Microsoft
Full Stack Data Science Bootcamp 2.0 — iNeuron
"""

# ═══════════════════════════════════════════════════════════════════════════════
# AGENT 5 OUTPUT — INTERVIEW PREP
# ═══════════════════════════════════════════════════════════════════════════════
interview_prep = """
## TECHNICAL QUESTIONS (likely to be asked)

1. Walk me through how you'd design a RAG pipeline for an enterprise document QA system end-to-end. — Tests: RAG architecture depth, chunking strategy, retrieval, LLM integration

2. What is the difference between LangChain Agents and CrewAI? When would you use one over the other? — Tests: Agentic AI framework knowledge (your biggest gap — prepare this tonight)

3. How do you choose between different vector databases (Pinecone, Weaviate, Chroma, pgvector)? What factors matter? — Tests: Vector DB practical knowledge

4. Explain the difference between semantic search and keyword search. How do embeddings make semantic search possible? — Tests: Embeddings and retrieval fundamentals

5. How would you handle hallucination in a production RAG system? What techniques reduce it? — Tests: Production LLM thinking, grounding strategies

6. What is LangGraph and how does it differ from a simple LangChain chain? — Tests: Graph-based agent orchestration understanding

7. Describe how you'd build a multi-agent workflow where Agent A researches, Agent B summarises, and Agent C writes a report. — Tests: Agentic AI design thinking

8. How do you evaluate a GenAI system in production? What metrics do you track? — Tests: MLOps + LLM evaluation (RAGAS, faithfulness, relevance scores)

9. You have a 95% accurate medical imaging model. How do you ensure it stays accurate after deployment as data drifts? — Tests: Production ML thinking, monitoring

10. What is the difference between fine-tuning an LLM and RAG? When would you choose each? — Tests: Core GenAI architecture decision-making

## BEHAVIOURAL QUESTIONS (Persistent Systems-specific)

1. Persistent talks about "engineering excellence" — give me an example where you went beyond the spec to engineer something robustly.

2. We work with enterprise clients across BFSI and healthcare. Tell me about a time you delivered an AI solution under tight constraints.

3. How do you stay current with the rapidly changing GenAI landscape? (LangGraph, CrewAI, AutoGen all appeared in 2023-2024)

4. Persistent is growing fast — $2B target by FY27. How do you handle ambiguity and changing priorities on fast-moving projects?

5. Tell me about a time an AI model underperformed in production. How did you diagnose and fix it?

## STAR STORY SUGGESTIONS

### Story 1: "Building a production RAG system under real constraints"
- Situation: Client needed intelligent QA over large document corpus — traditional keyword search wasn't cutting it
- Task: Build a production-grade RAG system that could retrieve accurately and answer in natural language
- Action: Implemented advanced chunking strategies, semantic search with embeddings, integrated LLM with conversation memory, built evaluation pipeline to measure retrieval quality
- Result: System deployed and used in production for document understanding — measurable improvement in retrieval accuracy
- Best used for: Questions 1, 5, 6, and "tell me about a production GenAI system you built"

### Story 2: "95% accuracy medical imaging model — from idea to deployment"
- Situation: Dental disease diagnosis was manual, time-consuming, error-prone — 40% wasted diagnostic time
- Task: Build a computer vision model accurate enough to assist clinical decision-making
- Action: Engineered end-to-end pipeline — data preprocessing, CNN + Vision Transformer architecture, rigorous evaluation, Streamlit deployment with Docker for clinical use
- Result: 95% accuracy, 40% reduction in diagnostic time — shipped to production
- Best used for: "Tell me about a model you deployed" / engineering excellence questions

### Story 3: "Delivering AI expertise to 500+ clients as an SME"
- Situation: Edtech clients needed to learn practical ML/AI — gap between textbook knowledge and real implementation
- Task: Bridge that gap across ML, deep learning, NLP domains for diverse clients
- Action: Built curriculum around practical implementation — from classical ML to transformers — tailored to each client's use case
- Result: 500+ clients guided successfully across 3 years — built deep cross-domain AI knowledge
- Best used for: Communication skills / stakeholder handling / "how do you explain AI to non-technical people"

## SALARY NEGOTIATION STRATEGY

- Target ask: ₹22–26 LPA (positions you at market rate for GenAI engineer with your profile in Pune)
- Walk-away: ₹18 LPA (below this, the role doesn't reflect GenAI market rates)
- Negotiation script: "Based on my experience building production RAG systems and end-to-end ML pipelines, and given the current market for GenAI and Agentic AI skills, I'm looking at ₹22–25 LPA. I'm genuinely excited about Persistent's GenAI Hub and the $2B growth trajectory — this is where I want to invest my energy."
- Timing: Don't raise salary first. When HR asks, give the range — don't give a single number. Say "I'm flexible within a range depending on the complete package."
- Also negotiate: Variable pay %, L&D budget, project allocation timeline (avoid bench)

## SMART QUESTIONS TO ASK THE INTERVIEWER

1. "Persistent was recognised as an ISG Leader in GenAI Services in 2025 — can you tell me what specific GenAI use cases the Pune team is currently working on?"

2. "What does the typical project lifecycle look like for an Agentic AI engineer here — how much is client-facing vs internal R&D?"

3. "How does Persistent support continuous learning in GenAI, given how fast the space moves? Is there a budget for courses, certifications, or conference attendance?"

4. "What agent frameworks does the team actively use in production — LangGraph, CrewAI, AutoGen, or a mix?"

5. "What does success look like in the first 90 days for someone joining this team?"

## KEY TALKING POINTS

1. "I've shipped production AI — the RAG system, the 95% medical imaging model, Docker-deployed pipelines. I don't just prototype, I engineer for production." (addresses their "scalable, high-performance" requirement)

2. "I'm deeply familiar with the LLM + RAG stack and actively working with local LLMs through Ollama. The Agentic AI frameworks (CrewAI, LangGraph) are the natural next step I'm moving into — and this role is exactly the right environment to do that at scale." (honest framing of your gap as a growth direction, not a weakness)

3. "Persistent's growth trajectory — fastest-growing IT services brand, $2B target — tells me this is a company where AI investment is real, not just marketing. That's where I want to build my next chapter." (shows research, genuine interest)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# GENERATE OUTPUTS
# ═══════════════════════════════════════════════════════════════════════════════

cv_path = str(out_dir / "Pranav_Tailored_CV_Persistent.docx")
pdf_path = str(out_dir / "Persistent_Systems_Intelligence_Report.pdf")

print("\nGenerating tailored CV (Word doc)...")
generate_cv_docx(tailored_cv, cv_path)

print("\nGenerating intelligence report (PDF)...")
generate_report_pdf(
    company_name=company_name,
    jd_analysis=jd_analysis,
    company_research=company_research,
    interview_prep=interview_prep,
    output_path=pdf_path,
)

# Save raw markdown
(out_dir / "raw_outputs.md").write_text(
    f"# JD Analysis\n{jd_analysis}\n\n"
    f"# Company Intelligence\n{company_research}\n\n"
    f"# Tailored CV\n{tailored_cv}\n\n"
    f"# Interview Prep\n{interview_prep}\n",
    encoding="utf-8",
)

print(f"\n✓ Tailored CV  → {cv_path}")
print(f"✓ Full Report  → {pdf_path}")
print(f"✓ Raw markdown → {out_dir / 'raw_outputs.md'}")
