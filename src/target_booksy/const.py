from typing import List

# ── Required config keys ────────────────────────────────────────────────────────
REQUIRED_CONFIG_KEYS: List[str] = [
    "tenant_domain",    # e.g. booksy.com
    "client_id",        # Azure AD app client ID
    "client_secret",    # Azure AD app client secret
    "environment",      # BC environment, e.g. Sandbox_UK
    "company_id",       # BC company GUID
    "input_path",       # directory containing JournalEntries.csv
]

# ── Auth (Microsoft OAuth2) ─────────────────────────────────────────────────────
AUTH_URL_TEMPLATE   = "https://login.microsoft.com/{tenant_domain}/oauth2/v2.0/token"
BC_SCOPE            = "https://api.businesscentral.dynamics.com/.default"
GRANT_TYPE          = "client_credentials"

# ── Business Central bulk batch API ────────────────────────────────────────────
# Replaces single-record bc_gj_lines. Accepts up to 100 records per call.
BC_BATCH_URL_TEMPLATE = (
    "https://api.businesscentral.dynamics.com/v2.0/{tenant_domain}/{environment}"
    "/api/Booksy/Chargebee/v1.0/$batch"
)

# URL used inside each batch request item (relative, no base URL)
BC_BATCH_ITEM_URL_TEMPLATE = "companies({company_id})/bc_gj_lines"

BATCH_SIZE              = 100  # max records per $batch call

DEFAULT_TIMEOUT_SECONDS = 30
MAX_RETRIES             = 4
RETRY_BACKOFF_BASE      = 2   # seconds, doubles each attempt

# ── Input CSV ───────────────────────────────────────────────────────────────────
INPUT_FILENAME = "JournalEntries.csv"

REQUIRED_CSV_COLS: List[str] = [
    "Transaction Date",
    "Posting Id",
    "Account Number",
    "Type",
    "Amount",
    "Currency Code",
]

# ── Fixed default values (hardcoded, not from config) ───────────────────────────
DEFAULT_BAL_ACCOUNT_NO  = "751-05-00"
DEFAULT_DESCRIPTION     = "Chargebee Monthly Subscription Revenue"

# ── CSV column names ────────────────────────────────────────────────────────────
COL_ORG_ID              = "OrgId"
COL_STATE               = "State"
COL_JOB_ID              = "Job Id"
COL_POSTING_GROUP_ID    = "Posting Group Id"
COL_POSTING_ID          = "Posting Id"           # → ext_document_no (grouping key)
COL_ACTG_PERIOD         = "Actg Period"
COL_TRANSACTION_DATE    = "Transaction Date"     # → posting_date + document_date
COL_ACCOUNTING_EVENT    = "Accounting Event Type"
COL_ACCOUNT_NAME        = "Account Name"
COL_ACCOUNT_NUMBER      = "Account Number"       # → account_no
COL_TYPE                = "Type"                 # Debit / Credit → amount sign
COL_CURRENCY_CODE       = "Currency Code"        # → currency_code
COL_AMOUNT              = "Amount"               # → amount (signed)
COL_CUSTOMER_ID         = "Customer Id"
COL_PRODUCT_ID          = "Product Id"
COL_LAST_MODIFIED       = "Last Modified Date"

# Dim columns — read from CSV if header present, otherwise sent as ""
COL_DIM_COST_CENTER     = "dim_cost_center"
COL_DIM_COUNTRY         = "dim_country"
COL_DIM_PROJECT         = "dim_project"
COL_DIM_TAX             = "dim_tax"

# ── BC API payload field names ──────────────────────────────────────────────────
BC_POSTING_DATE         = "posting_date"
BC_DOCUMENT_DATE        = "document_date"
BC_EXT_DOCUMENT_NO      = "ext_document_no"
BC_ACCOUNT_NO           = "account_no"
BC_BAL_ACCOUNT_NO       = "bal_account_no"
BC_DESCRIPTION          = "description"
BC_AMOUNT               = "amount"
BC_CURRENCY_CODE        = "currency_code"
BC_DIM_COST_CENTER      = "dim_cost_center"
BC_DIM_COUNTRY          = "dim_country"
BC_DIM_PROJECT          = "dim_project"
BC_DIM_TAX              = "dim_tax"

# ── Amount sign convention ──────────────────────────────────────────────────────
# Business Central: positive = Debit, negative = Credit
POSTING_TYPE_DEBIT  = "DEBIT"
POSTING_TYPE_CREDIT = "CREDIT"
