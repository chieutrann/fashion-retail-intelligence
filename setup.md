
# LLM Setup & Execution Guide

**Project:** H&M Fashion Retail Intelligence  
**Technology:** Ollama + Qwen3 + Streamlit + Microsoft Fabric OneLake

## 1. Install Ollama

Download and install Ollama for Windows:

https://ollama.com/download

## 2. Download the Local LLM

Open PowerShell and run:

```powershell
ollama pull qwen3:4b
```

Optional: Download the larger model:

```powershell
ollama pull qwen3:8b
```

## 3. Install Python Dependencies

Navigate to the project folder:

```powershell
cd E:\Porfolio\fashion-retail-intelligence
```

Install the required packages:

```powershell
python -m pip install streamlit pandas pyodbc ollama azure-identity python-dotenv
```

Ensure Microsoft ODBC Driver 18 for SQL Server is installed.

## 4. Configure Microsoft Fabric

Create or update the `.env` file:

```env
SERVER=your-fabric-sql-endpoint-hostname
DATABASE=HM_database
POWER_BI_URL=your-power-bi-embed-url
```

Replace the placeholders with your actual Fabric SQL endpoint and Power BI embed URL.

## 5. Run the Application

Start Streamlit using PowerShell:

```powershell
cd E:\Porfolio\fashion-retail-intelligence
python -m streamlit run main.py --server.port 8502
```

## 6. Access the Application

Open your browser:

http://localhost:8502

## 7. Application Workflow

1. Enter a business question in the Streamlit chat interface.
2. Ollama processes the question using the local Qwen3 model.
3. The LLM generates Microsoft Fabric T-SQL.
4. Review and modify the generated SQL if necessary.
5. Approve and execute the query against Microsoft Fabric.
6. View query results, charts and CSV exports.
7. Access the embedded Power BI dashboard.

## Architecture

```text
User
  |
  v
Streamlit Web Interface
  |
  v
Ollama - Qwen3 Local LLM
  |
  v
Microsoft Fabric SQL Analytics Endpoint
  |
  v
OneLake - H&M Retail Database
  |
  v
Query Results, Charts and Power BI
```

## Requirements

- Windows operating system
- Python installed
- Ollama installed
- Qwen3 model downloaded
- Microsoft ODBC Driver 18 for SQL Server
- Microsoft Fabric SQL endpoint access
- Internet connection and Microsoft authentication

**Note:** The LLM runs locally on your computer. Microsoft Fabric remains a cloud service and requires internet access and valid authentication.

```
python -m pip install -r requirements.txt
```