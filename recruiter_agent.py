#!/usr/bin/env python3
"""
Gmail Recruiter Agent
A background service monitoring multiple Gmail accounts for recruiter emails every 5 minutes
and automatically generating professional threaded replies or drafts.
"""

import os
import sys
import warnings

# Suppress benign urllib3 LibreSSL and Python EOL deprecation notices on macOS default Python
warnings.filterwarnings("ignore")

import time
import json
import base64
import signal
import re
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parseaddr
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import schedule
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "processed_emails.json"
LOG_PATH = BASE_DIR / "agent.log"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
]

# Set up logging to both console and agent.log
logger = logging.getLogger("recruiter_agent")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
console_handler.setFormatter(console_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)


def load_config() -> Dict[str, Any]:
    """Loads configuration from config.json, returning sensible defaults if missing."""
    default_config = {
        "check_interval_minutes": 5,
        "action_mode": "reply",
        "mark_as_read_after_reply": False,
        "apply_gmail_label": True,
        "gmail_label_name": "Recruiter-Replied",
        "credentials_file": "credentials.json",
        "state_file": "processed_emails.json",
        "accounts": [
            {"id": "account_1", "name": "Account 1", "token_file": "token_account1.json"},
            {"id": "account_2", "name": "Account 2", "token_file": "token_account2.json"},
        ],
        "candidate": {
            "name": "Candidate",
            "role": "Senior Java Full Stack Developer",
            "custom_message": "Thank you for reaching out regarding the opportunity. I am currently evaluating my next career move as a Senior Java Full Stack Developer. Please let me know the best time to schedule an initial discussion or share more details about the role."
        },
        "search": {
            "query": "is:unread (recruiter OR \"talent acquisition\" OR hiring OR \"job opportunity\" OR interview OR \"career opportunity\")",
            "ignore_senders": ["no-reply", "noreply", "donotreply", "jobalerts-noreply@linkedin.com"]
        }
    }

    if not CONFIG_PATH.exists():
        logger.warning(f"Config file not found at {CONFIG_PATH}. Using internal defaults.")
        return default_config

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading {CONFIG_PATH}: {e}. Fallback to default config.")
        return default_config


def load_state(state_file_path: Path) -> Dict[str, Any]:
    """Loads previously processed message state to prevent duplicate responses."""
    if not state_file_path.exists():
        return {"processed_message_ids": {}, "processed_thread_ids": {}}

    try:
        with open(state_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "processed_message_ids" not in data:
                data["processed_message_ids"] = {}
            if "processed_thread_ids" not in data:
                data["processed_thread_ids"] = {}
            return data
    except Exception as e:
        logger.error(f"Could not load state file {state_file_path}: {e}. Initializing fresh state.")
        return {"processed_message_ids": {}, "processed_thread_ids": {}}


def save_state(state_file_path: Path, state_data: Dict[str, Any]) -> None:
    """Safely saves processed state using atomic write."""
    temp_file = state_file_path.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
        temp_file.replace(state_file_path)
    except Exception as e:
        logger.error(f"Failed to save state file {state_file_path}: {e}")
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


def get_gmail_service(account_info: Dict[str, str], credentials_filename: str) -> Optional[Resource]:
    """
    Initializes and returns an authenticated Gmail API service resource for a given account.
    Refreshes token automatically if expired.
    """
    account_id = account_info.get("id", "default")
    account_name = account_info.get("name", account_id)
    token_filename = account_info.get("token_file", f"token_{account_id}.json")

    token_path = BASE_DIR / token_filename
    credentials_path = BASE_DIR / credentials_filename

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            logger.warning(f"[{account_name}] Corrupted token file {token_filename}: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info(f"[{account_name}] Refreshing expired OAuth2 access token...")
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            except Exception as e:
                logger.error(f"[{account_name}] Failed to refresh token: {e}")
                creds = None

        if not creds:
            if not credentials_path.exists():
                logger.error(f"[{account_name}] Missing {credentials_path}. Cannot authenticate.")
                return None
            try:
                logger.info(f"[{account_name}] Launching browser OAuth2 flow for initial setup...")
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0, prompt="select_account consent", access_type="offline")
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                logger.info(f"[{account_name}] Saved new token to {token_filename}")
            except Exception as e:
                logger.error(f"[{account_name}] Failed to complete OAuth2 login: {e}")
                return None

    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return service
    except Exception as e:
        logger.error(f"[{account_name}] Error building Gmail API client: {e}")
        return None


def get_or_create_label_id(service: Resource, label_name: str) -> Optional[str]:
    """Finds or creates a custom Gmail label (e.g. 'Recruiter-Replied') to tag handled emails."""
    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        for lbl in labels:
            if lbl.get("name", "").lower() == label_name.lower():
                return lbl.get("id")

        # Create the label if not found
        label_body = {
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
            "color": {
                "backgroundColor": "#16a765",
                "textColor": "#ffffff"
            }
        }
        new_label = service.users().labels().create(userId="me", body=label_body).execute()
        logger.info(f"Created new Gmail label '{label_name}' (ID: {new_label.get('id')})")
        return new_label.get("id")
    except Exception as e:
        logger.warning(f"Could not retrieve or create label '{label_name}': {e}")
        return None


def extract_recruiter_first_name(from_header: str) -> str:
    """
    Intelligently extracts the recruiter's first name from the 'From' header.
    Examples:
      - 'John Doe <john.doe@company.com>' -> 'John'
      - '"Smith, Jane" <jane@company.com>' -> 'Jane'
      - 'sarah.connor@talentrecruiting.com' -> 'Sarah'
      - '<unknown@generic.com>' -> 'there'
    """
    real_name, email_addr = parseaddr(from_header)

    generic_words = {
        "info", "careers", "jobs", "team", "support", "hiring", "talent",
        "hr", "recruitment", "recruiting", "admin", "system", "unknown",
        "no", "reply", "contact", "hello", "general", "office", "alert", "alerts"
    }

    if real_name:
        # Strip quotes and extraneous whitespace
        clean_name = real_name.strip("\"' ")
        if "," in clean_name:
            # Format: Last, First
            parts = clean_name.split(",")
            if len(parts) > 1 and parts[1].strip():
                candidate = parts[1].strip().split()[0].capitalize()
                candidate_clean = re.sub(r"[^a-zA-Z]", "", candidate)
                if candidate_clean.lower() not in generic_words and len(candidate_clean) > 1:
                    return candidate_clean.capitalize()
        # Format: First Last
        first_part = clean_name.split()[0].strip()
        first_clean = re.sub(r"[^a-zA-Z]", "", first_part)
        if first_clean.lower() not in generic_words and len(first_clean) > 1:
            return first_clean.capitalize()

    # Fallback: inspect the username part of the email address
    if email_addr and "@" in email_addr:
        username = email_addr.split("@")[0]
        # Match common patterns like john.doe, john_doe, john-doe
        parts = [p for p in re.split(r"[._\-+]", username) if p]
        if parts:
            candidate_first = re.sub(r"[^a-zA-Z]", "", parts[0])
            if candidate_first.isalpha() and len(candidate_first) > 1:
                if candidate_first.lower() not in generic_words:
                    return candidate_first.capitalize()

    return "there"


def is_automated_or_bot(from_header: str, subject_header: str, ignored_senders: List[str]) -> bool:
    """Checks whether the incoming message is an automated notification, newsletter, or bot."""
    from_lower = from_header.lower()
    subject_lower = subject_header.lower()

    # Disallow all LinkedIn, Dice, ZipRecruiter, Indeed, Monster, and GreatRecruiters job board/alert emails
    job_board_patterns = [
        "linkedin", "via linkedin",
        "dice", "dice.com",
        "ziprecruiter", "zip recruiter",
        "indeed", "indeedmail", "indeedapply",
        "monster", "monster.com",
        "greatrecruiters", "greatrecruiters.com"
    ]
    for board in job_board_patterns:
        if board in from_lower:
            return True

    if any(k in subject_lower for k in ["inmail", "indeed apply", "ziprecruiter", "monster alert", "dice alert"]):
        return True

    for pattern in ignored_senders:
        if pattern.lower() in from_lower:
            return True

    bot_indicators = [
        "automated notification",
        "do not reply",
        "no-reply",
        "noreply",
        "job alert",
        "daily digest",
        "weekly digest",
        "digest",
        "weekly summary",
        "unsubscribe",
        "mailer-daemon",
        "postmaster@",
        "system@",
        "notification@",
        "notifications@"
    ]
    for ind in bot_indicators:
        if ind in from_lower or ind in subject_lower:
            return True

    return False


def build_reply_email(
    to_email: str,
    subject: str,
    body_text: str,
    thread_id: str,
    message_id_header: str,
    references_header: Optional[str] = None
) -> Dict[str, Any]:
    """Constructs a standard RFC 2822 reply message formatted for Gmail API."""
    message = MIMEMultipart()
    message["To"] = to_email

    # Format Subject with 'Re: ' prefix if not already present
    if not subject.lower().startswith("re:"):
        message["Subject"] = f"Re: {subject}"
    else:
        message["Subject"] = subject

    if message_id_header:
        message["In-Reply-To"] = message_id_header
        new_references = f"{references_header} {message_id_header}".strip() if references_header else message_id_header
        message["References"] = new_references

    message.attach(MIMEText(body_text, "plain", "utf-8"))

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return {
        "raw": raw_message,
        "threadId": thread_id
    }


def generate_response_text(recruiter_name: str, config: Dict[str, Any]) -> str:
    """Generates the personalized response letter based on config template."""
    candidate = config.get("candidate", {})
    candidate_name = candidate.get("name", "Candidate")
    role = candidate.get("role", "Senior Java Full Stack Developer")
    custom_message = candidate.get("custom_message")
    phone = candidate.get("phone", "").strip()
    linkedin = candidate.get("linkedin_url", "").strip()
    portfolio = candidate.get("portfolio_url", "").strip()

    if not custom_message:
        body = (
            f"Hi {recruiter_name},\n\n"
            f"Thank you for reaching out regarding the opportunity. I am currently evaluating "
            f"my next career move as a {role}. "
            f"Please let me know the best time to schedule an initial discussion or share "
            f"more details (such as the job description, team scope, and compensation range) "
            f"about the position.\n\n"
        )
    else:
        # If custom message contains placeholder [Name], replace it
        rendered = custom_message.replace("[Name]", recruiter_name)
        body = f"Hi {recruiter_name},\n\n{rendered}\n\n"

    # Signature block
    body += f"Best regards,\n{candidate_name}\n"
    if role:
        body += f"{role}\n"
    if phone:
        body += f"Phone: {phone}\n"
    if linkedin:
        body += f"LinkedIn: {linkedin}\n"
    if portfolio:
        body += f"Portfolio / GitHub: {portfolio}\n"

    return body


def process_account_inbox(account: Dict[str, str], config: Dict[str, Any], state: Dict[str, Any]) -> int:
    """
    Checks an individual Gmail account for unread recruiter emails,
    evaluates candidates, and executes the reply or draft action.
    """
    account_id = account.get("id")
    account_name = account.get("name", account_id)
    credentials_file = config.get("credentials_file", "credentials.json")
    state_file = Path(config.get("state_file", "processed_emails.json"))
    action_mode = config.get("action_mode", "reply").lower()
    mark_as_read = config.get("mark_as_read_after_reply", False)
    apply_label = config.get("apply_gmail_label", True)
    label_name = config.get("gmail_label_name", "Recruiter-Replied")

    search_cfg = config.get("search", {})
    query = search_cfg.get("query", "is:unread (recruiter OR hiring OR \"job opportunity\")")
    ignored_senders = search_cfg.get("ignore_senders", [])

    logger.info(f"🔍 [{account_name}] Checking inbox with query: '{query}'")

    service = get_gmail_service(account, credentials_file)
    if not service:
        logger.warning(f"⚠️ [{account_name}] Skipping check due to authentication failure.")
        return 0

    label_id = None
    if apply_label:
        label_id = get_or_create_label_id(service, label_name)

    try:
        response = service.users().messages().list(userId="me", q=query, maxResults=20).execute()
        messages = response.get("messages", [])
    except HttpError as err:
        logger.error(f"❌ [{account_name}] Gmail API list error: {err}")
        return 0
    except Exception as e:
        logger.error(f"❌ [{account_name}] Unexpected error during message list: {e}")
        return 0

    if not messages:
        logger.info(f"✨ [{account_name}] No matching unread recruiter emails found.")
        return 0

    logger.info(f"📬 [{account_name}] Found {len(messages)} unread candidate message(s). Processing...")
    processed_count = 0

    for msg_meta in messages:
        msg_id = msg_meta.get("id")
        thread_id = msg_meta.get("threadId")

        # Skip if message or entire thread was already handled earlier
        if msg_id in state["processed_message_ids"]:
            logger.debug(f"[{account_name}] Message {msg_id} was already processed earlier. Skipping.")
            continue

        if thread_id and thread_id in state.get("processed_thread_ids", {}):
            logger.debug(f"[{account_name}] Thread {thread_id} was already processed earlier. Skipping.")
            state["processed_message_ids"][msg_id] = {
                "action": "skipped_thread_already_handled",
                "timestamp": time.time()
            }
            save_state(state_file, state)
            continue

        try:
            full_msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date", "Message-ID", "References"]
            ).execute()

            headers = {h.get("name"): h.get("value") for h in full_msg.get("payload", {}).get("headers", [])}
            from_header = headers.get("From", "")
            subject_header = headers.get("Subject", "(No Subject)")
            message_id_header = headers.get("Message-ID", "")
            references_header = headers.get("References", "")
            date_header = headers.get("Date", "")

            # Verify it's not automated / bot / alert
            if is_automated_or_bot(from_header, subject_header, ignored_senders):
                logger.info(f"🤖 [{account_name}] Ignored bot/alert message: '{subject_header}' from '{from_header}'")
                state["processed_message_ids"][msg_id] = {
                    "action": "ignored_bot",
                    "from": from_header,
                    "subject": subject_header,
                    "timestamp": time.time()
                }
                save_state(state_file, state)
                continue

            # Extract recruiter's name
            recruiter_name = extract_recruiter_first_name(from_header)
            reply_body = generate_response_text(recruiter_name, config)

            reply_payload = build_reply_email(
                to_email=from_header,
                subject=subject_header,
                body_text=reply_body,
                thread_id=thread_id,
                message_id_header=message_id_header,
                references_header=references_header
            )

            # Determine whether to send reply directly or create a draft
            action_result = {}
            if action_mode == "draft":
                draft_resp = service.users().drafts().create(
                    userId="me",
                    body={"message": reply_payload}
                ).execute()
                draft_id = draft_resp.get("id")
                logger.info(
                    f"📝 [{account_name}] Created DRAFT reply to '{from_header}' (Subject: '{subject_header}', Draft ID: {draft_id})"
                )
                action_result = {"status": "draft_created", "draft_id": draft_id}
            else:
                sent_resp = service.users().messages().send(
                    userId="me",
                    body=reply_payload
                ).execute()
                sent_id = sent_resp.get("id")
                logger.info(
                    f"🚀 [{account_name}] SENT REPLY to '{from_header}' (Subject: '{subject_header}', Sent ID: {sent_id})"
                )
                action_result = {"status": "reply_sent", "sent_id": sent_id}

            # Modify labels: add custom label and optionally remove UNREAD
            mods = {}
            if label_id:
                mods["addLabelIds"] = [label_id]
            if mark_as_read:
                mods["removeLabelIds"] = ["UNREAD"]

            if mods:
                try:
                    service.users().messages().modify(userId="me", id=msg_id, body=mods).execute()
                except Exception as mod_err:
                    logger.warning(f"[{account_name}] Could not update message labels: {mod_err}")

            # Record state
            state["processed_message_ids"][msg_id] = {
                "account": account_id,
                "from": from_header,
                "recruiter_name": recruiter_name,
                "subject": subject_header,
                "date": date_header,
                "action": action_result,
                "timestamp": time.time()
            }
            state["processed_thread_ids"][thread_id] = time.time()
            save_state(state_file, state)

            processed_count += 1

        except HttpError as err:
            logger.error(f"❌ [{account_name}] API error processing message {msg_id}: {err}")
        except Exception as ex:
            logger.error(f"❌ [{account_name}] Unexpected error processing message {msg_id}: {ex}", exc_info=True)

    return processed_count


def run_monitoring_cycle(config: Dict[str, Any], state: Dict[str, Any]) -> None:
    """Executes a single monitoring cycle across all configured Gmail accounts."""
    config = load_config()  # Dynamic reload so config changes take effect immediately
    logger.info("------------------------------------------------------------")
    logger.info("⏰ Starting scheduled recruiter email scan across accounts...")
    accounts = config.get("accounts", [])
    total_handled = 0

    for acc in accounts:
        try:
            handled = process_account_inbox(acc, config, state)
            total_handled += handled
        except Exception as e:
            logger.error(f"❌ Error while scanning account {acc.get('name', 'unknown')}: {e}", exc_info=True)

    logger.info(f"🏁 Scan complete. Total actions taken: {total_handled}")
    logger.info("------------------------------------------------------------\n")


def setup_graceful_shutdown():
    """Ensures clean shutdown when terminated via SIGINT or SIGTERM."""
    def handle_signal(sig, frame):
        logger.info(f"\n🛑 Received signal {sig}. Gracefully stopping Gmail Recruiter Agent...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main():
    print("""
========================================================================
   📧 GMAIL RECRUITER AGENT - MULTI-ACCOUNT BACKGROUND MONITOR 📧
========================================================================
    """)
    setup_graceful_shutdown()

    config = load_config()
    state_file = BASE_DIR / config.get("state_file", "processed_emails.json")
    state = load_state(state_file)

    interval_minutes = int(config.get("check_interval_minutes", 5))
    accounts = config.get("accounts", [])
    action_mode = config.get("action_mode", "reply")

    logger.info(f"Initialized agent with {len(accounts)} account(s).")
    logger.info(f"Action Mode: '{action_mode.upper()}' | Scan Interval: Every {interval_minutes} minutes.")
    logger.info(f"Logging active: Console + File ({LOG_PATH})")

    # Run initial cycle immediately upon startup
    logger.info("🚀 Executing initial scan immediately...")
    run_monitoring_cycle(config, state)

    # Schedule subsequent runs every N minutes
    schedule.every(interval_minutes).minutes.do(run_monitoring_cycle, config=config, state=state)

    logger.info(f"⏳ Background scheduler running. Waiting for next trigger in {interval_minutes} minutes...")
    logger.info("Press Ctrl+C to terminate cleanly.\n")

    # Main infinite polling loop with rate-limiting and connection tolerance
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Termination requested by user. Exiting.")
            break
        except Exception as loop_err:
            logger.error(f"Unhandled error in main schedule loop: {loop_err}", exc_info=True)
            time.sleep(10)  # Pause to avoid hot looping if an unexpected system exception occurs


if __name__ == "__main__":
    main()
