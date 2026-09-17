import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings:
    """Application settings and configuration"""

    # ---- AZURE BLOB STORAGE ----
    AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "weekly-summary")
    DEFAULT_INDEX_PATH = os.getenv("INDEX_BLOB_PATH", "index/2025-08-28.json")
    DEFAULT_REPORTS_PREFIX = os.getenv("REPORTS_PREFIX", "2025-08-28")

    # ---- APPLICATION AZURE SQL ----
    APP_AZURE_SQL_SERVER = os.getenv("APP_AZURE_SQL_SERVER", "")
    APP_AZURE_SQL_DATABASE = os.getenv("APP_AZURE_SQL_DATABASE", "")
    APP_AZURE_SQL_PORT = os.getenv("APP_AZURE_SQL_PORT", "1433")
    APP_AZURE_SQL_DRIVER = os.getenv("APP_AZURE_SQL_DRIVER", "{ODBC Driver 18 for SQL Server}")
    APP_AZURE_SQL_LOGIN_TIMEOUT = int(os.getenv("APP_AZURE_SQL_LOGIN_TIMEOUT", "90"))
    APP_AZURE_SQL_QUERY_TIMEOUT = int(os.getenv("APP_AZURE_SQL_QUERY_TIMEOUT", "0"))

    DEFAULT_PROGRAM: str = os.getenv("DEFAULT_PROGRAM", "telesales")
    TELESALES_PROGRAMS = os.getenv("TELESALES_PROGRAMS", "telesales,Telesales").split(",")
    WCC_PROGRAMS = os.getenv("WCC_PROGRAMS", "wcc,BGCO").split(",")
    PSO_PROGRAMS = os.getenv("PSO_PROGRAMS", "pso,PSO").split(",")

    # ---- CHATBOT API ----
    # Base URL of the chatbot FastAPI service consumed by the floating chat
    # widget. Timeout is the client-side abort budget (seconds), kept above the
    # backend's worst case (cold Azure SQL resume + LLM) to avoid spurious
    # timeouts when the backend actually succeeds.
    CHAT_API_BASE_URL = os.getenv("CHAT_API_BASE_URL", "http://localhost:8000")
    CHAT_REQUEST_TIMEOUT = int(os.getenv("CHAT_REQUEST_TIMEOUT", "120"))

    # Shared secret used to mint the short-lived JWT the chat widget sends to the
    # chatbot API. MUST match CHAT_JWT_SECRET on the chatbot service. The token
    # lifetime (minutes) bounds how long a leaked token stays valid.
    CHAT_JWT_SECRET = os.getenv("CHAT_JWT_SECRET", "")
    CHAT_JWT_ALG = os.getenv("CHAT_JWT_ALG", "HS256")
    CHAT_JWT_ISSUER = os.getenv("CHAT_JWT_ISSUER", "apix-app")
    CHAT_JWT_AUDIENCE = os.getenv("CHAT_JWT_AUDIENCE", "apix-chatbot")
    CHAT_JWT_TTL_MINUTES = int(os.getenv("CHAT_JWT_TTL_MINUTES", "30"))
    
    # ---- AUTHENTICATION ----
    # All user credentials (manager, coach, agent) are stored in auth.db.
    # Use db_crud.py to seed or update users. For production, replace
    # password auth with OAuth/SSO via sso.py + app/auth/system.py.

    # ---- CACHING ----
    CACHE_TTL_SECONDS = 300  # Data cache time-to-live
    INDEX_CACHE_TTL = 600  # Index cache time-to-live

    # ---- PERFORMANCE SCORING CONSTANTS ----
    # VXS Quality Scores
    VXS_WEIGHT = 0.40  # 40% of total score
    VXS_MIN_THRESHOLD = 70  # Below this is concerning
    VXS_CRITICAL_THRESHOLD = 60  # Below this is critical

    # Sales Performance
    SALES_WEIGHT = 0.30  # 30% of total score
    NEW_LINE_TARGET = 40  # Expected new line pitches
    PROTECTION_TARGET = 30  # Expected mobile protection
    SAVE_ATTEMPTS_TARGET = 10  # Expected save attempts

    # Trend Analysis
    TREND_WEIGHT = 0.20  # 20% of total score
    TREND_DECLINE_THRESHOLD = -3  # Mark as risk if delta < -3
    TREND_CRITICAL_THRESHOLD = -5  # Mark as critical if delta < -5

    # Quality & Escalations
    QUALITY_WEIGHT = 0.10  # 10% of total score
    ESCALATION_THRESHOLD_HIGH = 2  # High risk above this
    ESCALATION_THRESHOLD_CRITICAL = 5  # Critical above this

    # ---- RISK SCORING ----
    RISK_DELTA_DECLINE = -3  # Mark delta decline as risk
    RISK_DELTA_CRITICAL = -5  # Mark as critical

    # ---- STREAMLIT PAGE CONFIG ----
    PAGE_TITLE = "IntelInsights Sales"
    PAGE_ICON = "📊"
    LAYOUT = "wide"
    SIDEBAR_STATE = "expanded"

    # ---- DIRECTORIES ----
    LOGS_DIR = Path("logs")
    AUDIT_LOG_FILE = LOGS_DIR / "audit_log.jsonl"

    # ---- AUTH MAPPING ----
    # Local path to manager->coach UPN map. Default resolves relative to the
    # application/ directory so code works regardless of current working dir.
    UPN_MAP_PATH = os.getenv(
        "UPN_MAP_PATH",
        str(Path(__file__).resolve().parents[1] / "auth" / "manager_coach_hierarchy.json"),
    )
    UPN_MAP_BLOB_PATH = os.getenv("UPN_MAP_BLOB_PATH", "index/manager_coach_hierarchy.json")

    @classmethod
    def validate(cls):
        """Validate critical settings are configured"""
        if not cls.AZURE_BLOB_CONNECTION_STRING:
            raise ValueError(
                "AZURE_BLOB_CONNECTION_STRING is not set. "
                "Please set it in your .env file."
            )

settings = Settings()
