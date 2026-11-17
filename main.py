import re
import os
import struct

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import ollama
import pyodbc
from dotenv import load_dotenv
from azure.identity import InteractiveBrowserCredential

# -----------------------------
# PAGE CONFIG
# -----------------------------
st.set_page_config(page_title="H&M Retail Intelligence", page_icon="🛍️", layout="wide")
st.title("🛍️ H&M Retail Intelligence")
st.caption("AI-powered retail analytics | Local AI + Microsoft Fabric")

assistant_tab, dashboard_tab = st.tabs(
    [
        "🤖 AI Assistant",
        "📊 Power BI Dashboard"
    ]
)

with assistant_tab:
    # -----------------------------
    # FABRIC CONNECTION
    # -----------------------------
    load_dotenv(".env")
    SERVER = os.getenv("SERVER", "").strip()
    DATABASE = os.getenv("DATABASE", "HM_database").strip()
    POWER_BI_URL = os.getenv("POWER_BI_URL", "").strip()


    @st.cache_resource
    def get_credential():
        # Keep one credential so the browser login happens only once.
        return InteractiveBrowserCredential()


    @st.cache_resource(ttl=2700)  # Entra tokens last ~60 min; reconnect before expiry
    def get_connection():
        token = get_credential().get_token("https://database.windows.net/.default").token
        token_bytes = token.encode("utf-16-le")
        token_struct = struct.pack("<I", len(token_bytes)) + token_bytes

        conn_str = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER=tcp:{SERVER},1433;"
            f"DATABASE={DATABASE};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
        )
        return pyodbc.connect(conn_str, attrs_before={1256: token_struct})  # SQL_COPT_SS_ACCESS_TOKEN


    @st.cache_data(ttl=600)
    def get_schema_text(_conn_id: int, _conn) -> str:
        """Schema-qualified column list for user tables/views only (no sys / INFORMATION_SCHEMA)."""
        df = pd.read_sql_query(
            """
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA', 'queryinsights')
            ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
            """,
            _conn,
        )
        lines = []
        for (schema, table), g in df.groupby(["TABLE_SCHEMA", "TABLE_NAME"], sort=False):
            cols = ", ".join(f"{r.COLUMN_NAME} ({r.DATA_TYPE})" for r in g.itertuples())
            lines.append(f"[{schema}].[{table}]: {cols}")
        return "\n".join(lines)


    # -----------------------------
    # SQL HELPERS
    # -----------------------------
    FORBIDDEN = re.compile(
        r"\b(insert|update|delete|drop|alter|create|truncate|merge|exec|execute|"
        r"grant|revoke|deny|copy|openrowset|openquery|xp_\w+|sp_\w+)\b",
        re.IGNORECASE,
    )


    def clean_sql(raw: str) -> str:
        """Strip <think> blocks (Qwen3), markdown fences, comments and trailing semicolons."""
        sql = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        fence = re.search(r"```(?:sql|tsql)?\s*(.*?)```", sql, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            sql = fence.group(1)
        sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        return sql.strip().rstrip(";").strip()


    def to_fabric_tsql(sql: str) -> str:
        """Rewrite common Spark/MySQL/Postgres habits into Fabric Warehouse T-SQL."""
        sql = sql.replace("`", "")                                   # backticks -> none
        sql = re.sub(r"\bILIKE\b", "LIKE", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_DATE\b(?!\s*\()", "CAST(GETDATE() AS date)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bNOW\(\)", "GETDATE()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bIFNULL\(", "ISNULL(", sql, flags=re.IGNORECASE)

        # Trailing LIMIT n  ->  SELECT TOP n
        m = re.search(r"\bLIMIT\s+(\d+)\s*$", sql, flags=re.IGNORECASE)
        if m:
            n = m.group(1)
            sql = sql[: m.start()].rstrip()
            if not re.search(r"^\s*(select\s+(distinct\s+)?top\b)", sql, flags=re.IGNORECASE):
                sql = re.sub(r"^(\s*(?:with\b.*?\)\s*)?select)(\s+distinct)?\b",
                            lambda mm: f"{mm.group(1)}{mm.group(2) or ''} TOP {n}",
                            sql, count=1, flags=re.IGNORECASE | re.DOTALL)
        return sql.strip()


    def validate_sql(sql: str):
        """Return an error message, or None when the query looks safe. Demo guard, not a security boundary
        (use a read-only Fabric role/permissions for real protection)."""
        if not re.match(r"^\s*(select|with)\b", sql, flags=re.IGNORECASE):
            return "Only SELECT queries (optionally starting with WITH) are allowed."
        if ";" in sql:
            return "Only a single statement is allowed."
        if FORBIDDEN.search(sql):
            return "Query contains a forbidden keyword."
        return None


    def build_prompt(schema_text: str, question: str, previous_sql: str = "", error: str = "") -> str:
        repair = ""
        if previous_sql and error:
            repair = f"""
    The previous query failed in Microsoft Fabric.
    Previous SQL:
    {previous_sql}
    Error:
    {error}
    Fix the query so it runs successfully.
    """
        return f"""
    You are an expert retail data analyst writing queries for a Microsoft Fabric Warehouse (T-SQL).

    Generate ONE read-only T-SQL SELECT query for the user's question.

    Database schema (schema-qualified tables with columns and types):
    {schema_text}

    Rules:
    - Use only tables and columns that appear in the schema. Reference tables as [schema].[table].
    - Fabric Warehouse T-SQL only. NOT Spark SQL, MySQL or PostgreSQL.
    - Row limits: use SELECT TOP (n) ... never LIMIT or FETCH without OFFSET.
    - ORDER BY is not allowed in subqueries/CTEs/views unless TOP is used there.
    - Dates: use YEAR(), MONTH(), DATEPART(), DATEFROMPARTS(), EOMONTH(), FORMAT(date,'yyyy-MM'). No DATE_TRUNC or DATE_FORMAT.
    - Strings: use + or CONCAT(), LEN(), SUBSTRING(). Use [brackets] for names, never backticks.
    - Give every computed column an alias. Cast SUM/COUNT results to avoid overflow where needed (e.g. CAST(SUM(price) AS DECIMAL(18,2))).
    - When ranking, use TOP (10) with ORDER BY unless the user asks for more.
    - Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, EXEC.
    - Do not invent an order_id column. transactions contains transaction item records.
    - SUM(price) represents sales value.
    - Return SQL only, no explanation, no markdown fences.
    {repair}
    User question: {question}
    """
    @st.cache_data(persist="disk")
    def generate_sql(model: str, prompt: str) -> str:
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        return to_fabric_tsql(clean_sql(resp["message"]["content"]))


    # -----------------------------
    # SESSION STATE
    # -----------------------------
    for key, default in {
        "messages": [],
        "pending_sql": None,
        "last_question": "",
        "last_error": "",
        "result_df": None,
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default

    # -----------------------------
    # SIDEBAR
    # -----------------------------
    with st.sidebar:
        st.header("⚙️ Settings")
        st.subheader("Connection")
        try:
            conn = get_connection()
            conn.cursor().execute("SELECT 1").fetchone()  # detect stale connection
            st.success("Fabric connected")
        except Exception as e:
            get_connection.clear()
            try:
                conn = get_connection()
                st.success("Fabric reconnected")
            except Exception as e2:
                st.error("Fabric connection failed")
                st.caption(str(e2))
                conn = None

        st.divider()
        st.subheader("Local AI")
        model = st.selectbox("Choose model", ["qwen3:4b", "qwen3:8b"])
        st.caption("AI runs locally through Ollama.")

        if st.button("Clear conversation"):
            for k in ("messages", "pending_sql", "last_question", "last_error", "result_df"):
                st.session_state[k] = [] if k == "messages" else (None if k in ("pending_sql", "result_df") else "")
            st.rerun()

    # -----------------------------
    # MAIN LAYOUT
    # -----------------------------
    chat_col, result_col = st.columns([1.1, 1])

    with chat_col:
        st.subheader("💬 Ask your data")

        suggestions = [
            "Which product groups generate the most revenue?",
            "Who are the highest-value customers?",
            "What are the monthly sales trends?",
            "Which colours are most popular?",
            "What products should we recommend?",
        ]
        for q in suggestions:
            if st.button(q, use_container_width=True):
                st.session_state.user_question = q

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        user_input = st.chat_input("Ask a question about H&M retail data...")
        if "user_question" in st.session_state:
            user_input = st.session_state.pop("user_question")

        if user_input:
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.markdown(user_input)

            if conn is None:
                st.error("Please establish the Fabric connection first.")
            else:
                schema_text = get_schema_text(id(conn), conn)
                with st.chat_message("assistant"):
                    with st.spinner("Generating T-SQL with local AI..."):
                        sql_out = generate_sql(model, build_prompt(schema_text, user_input))
                    st.session_state.pending_sql = sql_out
                    st.session_state.sql_editor = sql_out
                    st.session_state.last_question = user_input
                    st.session_state.last_error = ""
                    st.session_state.result_df = None
                    msg = "SQL generated. Review and approve it in the results panel."
                    st.markdown(msg)
                    st.session_state.messages.append({"role": "assistant", "content": msg})

    # -----------------------------
    # SQL PREVIEW AND RESULTS
    # -----------------------------
    with result_col:
        st.subheader("🧠 SQL & Results")
        sql = st.session_state.pending_sql

        if not sql:
            st.info("Ask a question to generate SQL. Your query and results will appear here.")
        else:
            st.markdown("**Generated T-SQL** (editable)")
            sql = st.text_area(
                "Generated T-SQL (editable)",
                key="sql_editor",
                height=180,
                label_visibility="collapsed"
            )

            st.session_state.pending_sql = sql

            approved = st.checkbox("I have reviewed this query and approve execution.")

            if st.button("▶ Run SQL query", disabled=not approved or conn is None, use_container_width=True):
                final_sql = to_fabric_tsql(clean_sql(sql))
                problem = validate_sql(final_sql)
                if problem:
                    st.error(problem)
                else:
                    try:
                        with st.spinner("Executing query in Fabric..."):
                            st.session_state.result_df = pd.read_sql_query(final_sql, conn)
                        st.session_state.last_error = ""
                    except Exception as e:
                        st.session_state.result_df = None
                        st.session_state.last_error = str(e)

            if st.session_state.last_error:
                st.error(f"Query failed: {st.session_state.last_error}")
                if conn is not None and st.button("🔧 Ask AI to fix this query", use_container_width=True):
                    with st.spinner("Repairing SQL..."):
                        schema_text = get_schema_text(id(conn), conn)
                        st.session_state.pending_sql = generate_sql(
                            model,
                            build_prompt(
                                schema_text,
                                st.session_state.last_question,
                                previous_sql=sql,
                                error=st.session_state.last_error,
                            ),
                        )
                        st.session_state.last_error = ""
                    st.rerun()

            df = st.session_state.result_df
            if df is not None:
                st.success(f"Returned {len(df):,} rows")
                st.dataframe(df, use_container_width=True, height=350)

                if not df.empty:
                    numeric_cols = df.select_dtypes(include="number").columns.tolist()
                    if numeric_cols:
                        metric = st.selectbox("Choose a metric to visualize", numeric_cols)
                        label_col = next((c for c in df.columns if c not in numeric_cols), df.columns[0])
                        st.bar_chart(df.set_index(label_col)[metric])

                    st.download_button(
                        "⬇ Download results as CSV",
                        df.to_csv(index=False).encode("utf-8"),
                        "hm_analysis.csv",
                        "text/csv",
                    )


with dashboard_tab:
    # -----------------------------
    # EMBEDDED POWER BI REPORT
    # -----------------------------
    st.subheader("📊 Power BI Dashboard")

    if POWER_BI_URL:
        components.iframe(POWER_BI_URL, height=650, scrolling=False)
    else:
        st.info("Set POWER_BI_URL in your .env file to embed the Power BI report here.")