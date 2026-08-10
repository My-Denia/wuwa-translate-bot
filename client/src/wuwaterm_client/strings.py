"""Every user-facing literal in the WuwaTerm desktop client, in one place.

The ui/ package and errors.py never define display text inline; they import
constants from this module. tests/test_ui_strings_source.py statically
checks that ui/*.py contains no other literal text passed to a text-setting
call, so this module is the single source of truth for what a user can see.
"""

from __future__ import annotations

# -- Application chrome ----------------------------------------------------

APP_TITLE = "WuwaTerm"

MENU_FILE = "File"
MENU_QUIT = "Quit"
MENU_HELP = "Help"
MENU_ABOUT = "About"
ABOUT_TEXT = "WuwaTerm desktop client. Calls the wuwaterm HTTP API and renders its responses."

STATUS_BAR_READY = "Ready"
STATUS_BAR_TRANSLATING = "Translating..."
STATUS_BAR_SEARCHING = "Searching..."

STATUS_UNKNOWN_VALUE = "Unknown"
STATUS_YES = "Yes"
STATUS_NO = "No"
STATUS_LOADING = "Loading..."

# -- Direction selector ------------------------------------------------

DIRECTION_AUTO = "Auto"
DIRECTION_TO_EN = "Chinese to English"
DIRECTION_TO_ZH = "English to Chinese"

# -- Translate tab -----------------------------------------------------

TRANSLATE_TAB_TITLE = "Translate"
INPUT_LABEL = "Source text"
INPUT_PLACEHOLDER = "Enter text to translate..."
DIRECTION_LABEL = "Direction"
TRANSLATE_BUTTON = "Translate"
CANCEL_BUTTON = "Cancel"
RESULT_LABEL = "Result"
RESULT_PLACEHOLDER = "The translation will appear here."

KIND_LABEL_EXACT = "Exact dictionary match"
KIND_LABEL_FUZZY = "Fuzzy dictionary match"
KIND_LABEL_LLM = "Model translation"
KIND_LABEL_NOOP = "Nothing to translate"

DICTIONARY_MISS_NOTE = (
    "No official term was matched; this answer is not authoritative."
)
REQUEST_ID_LABEL = "Request ID: {request_id}"

# -- Term lookup tab -----------------------------------------------------

TERMS_TAB_TITLE = "Term Lookup"
TERMS_QUERY_LABEL = "Search term"
TERMS_QUERY_PLACEHOLDER = "Type a term to look up..."
TERMS_SEARCH_BUTTON = "Search"
TERMS_COLUMN_ZH = "Chinese"
TERMS_COLUMN_EN = "English"
TERMS_COLUMN_CATEGORY = "Category"
TERMS_COLUMN_SCORE = "Score"
TERMS_COLUMN_REASON = "Reason"
TERMS_EMPTY = "No matches found."

# -- Status tab -----------------------------------------------------

STATUS_TAB_TITLE = "Service Status"
STATUS_SERVICE_VERSION_LABEL = "Service version"
STATUS_DATA_PROFILE_LABEL = "Data profile"
STATUS_DATA_COMMIT_LABEL = "Data commit"
STATUS_TERM_COUNT_LABEL = "Term count"
STATUS_MODEL_CONFIGURED_LABEL = "Translation model configured"
STATUS_REFRESH_BUTTON = "Refresh"
STATUS_KEYRING_BACKEND_LABEL = "Credential store backend"

# -- Settings dialog -----------------------------------------------------

SETTINGS_TITLE = "Settings"
SETTINGS_MENU_LABEL = "Settings..."
SETTINGS_BASE_URL_LABEL = "Server address"
SETTINGS_BASE_URL_PLACEHOLDER = "http://127.0.0.1:8787"
SETTINGS_TIMEOUT_LABEL = "Request timeout (seconds)"
SETTINGS_CREDENTIAL_SECTION_TITLE = "Device credential"
SETTINGS_ENTER_TOKEN_BUTTON = "Enter token..."
SETTINGS_CHANGE_TOKEN_BUTTON = "Change token..."
SETTINGS_FORGET_TOKEN_BUTTON = "Forget token"
SETTINGS_TOKEN_STATUS_STORED = "A device credential is stored."
SETTINGS_TOKEN_STATUS_MISSING = "No device credential is stored."

CONFIRM_FORGET_TOKEN_TITLE = "Forget device credential"
CONFIRM_FORGET_TOKEN_MESSAGE = (
    "Remove the stored device credential from this computer?"
)

# -- Token entry (shared by first run and settings) -----------------------

TOKEN_DIALOG_TITLE = "Enter device token"
TOKEN_DIALOG_LABEL = "Device token"

# -- First-run flow -----------------------------------------------------

FIRST_RUN_TITLE = "Welcome to WuwaTerm"
FIRST_RUN_MESSAGE = (
    "Enter the device token you received from the service operator to continue."
)
FIRST_RUN_TOKEN_PLACEHOLDER = "Paste device token here"
FIRST_RUN_CONTINUE_BUTTON = "Continue"
FIRST_RUN_QUIT_BUTTON = "Quit"

# -- Error / status messages -----------------------------------------------
# Keys mirror the stable error codes in errors.py (server codes plus the
# client-only transport/cancellation states). Message text lives only here.

ERROR_MSG_UNAUTHORIZED = (
    "The stored device credential was rejected. Enter a new token in Settings."
)
ERROR_MSG_FORBIDDEN = "This device credential does not have permission for that action."
ERROR_MSG_RATE_LIMITED = "Too many requests. Wait a moment and try again."
ERROR_MSG_PAYLOAD_TOO_LARGE = "That text is too large to send."
ERROR_MSG_INVALID_REQUEST = "The request was not valid."
ERROR_MSG_INPUT_TOO_LONG = "That text is longer than the service allows."
ERROR_MSG_LLM_UNAVAILABLE = "The translation model is currently unavailable."
ERROR_MSG_LLM_BUDGET_EXHAUSTED = "The translation call budget is exhausted for now."
ERROR_MSG_INTERNAL = "The service reported an internal error."
ERROR_MSG_OFFLINE = "Could not reach the server. Check the server address and your connection."
ERROR_MSG_TIMEOUT = "The request timed out."
ERROR_MSG_UNKNOWN = "An unexpected error occurred."
STATUS_CANCELLED = "Translation cancelled."
