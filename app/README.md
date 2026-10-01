# Suraksha Streamlit app

Four persona pages: Trade-ops analyst (intake queue), Investigator (plain-English linkage Q&A), Compliance officer / MLRO (STR review, approve/reject, audit chain verify), Risk head (KPIs, exposure, daily summary).

    pip install -e ".[app]"   # or: pip install streamlit pandas
    streamlit run app/streamlit_app.py

Data: synthetic set (seed 42) is generated into a MemoryStore and run through `Suraksha.process` once per server (`st.cache_resource`). `SURAKSHA_BACKEND=memory` is the only wired backend. Decisions persist for the life of the server process.

Snowflake: only streamlit, pandas, stdlib and suraksha are imported; no network calls.
