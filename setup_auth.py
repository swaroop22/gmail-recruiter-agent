#!/usr/bin/env python3
"""
Gmail Recruiter Agent - Authentication & Account Setup Utility
Authorizes Account 1 and Account 2 via OAuth2 and creates local token files.
"""

import os
import sys
import warnings

# Suppress benign warnings
warnings.filterwarnings("ignore")

import json
import logging
import argparse
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("auth_setup")


def load_config():
    if not CONFIG_PATH.exists():
        logger.error(f"Configuration file not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config_data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not update config.json: {e}")


def authenticate_account(
    account_id: str,
    account_name: str,
    token_filename: str,
    credentials_filename: str,
    force_reauth: bool = False,
    existing_emails: dict = None
):
    token_path = BASE_DIR / token_filename
    credentials_path = BASE_DIR / credentials_filename

    print("\n" + "=" * 70)
    print(f"🔑 AUTHENTICATION SETUP FOR: {account_name} [{account_id}]")
    print("=" * 70)

    if not credentials_path.exists():
        print(f"\n❌ ERROR: OAuth credentials file '{credentials_filename}' not found at:")
        print(f"   {credentials_path}")
        print("\nPlease follow the instructions in README.md to download your")
        print("credentials.json from Google Cloud Console and place it in this directory.")
        return False, None

    creds = None
    if token_path.exists() and not force_reauth:
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            logger.info(f"Loaded existing token from {token_filename}")
        except Exception as e:
            logger.warning(f"Existing token is invalid or corrupt: {e}. Re-authenticating...")
            creds = None

    if force_reauth or not creds or not creds.valid:
        if not force_reauth and creds and creds.expired and creds.refresh_token:
            try:
                logger.info(f"Refreshing expired token for {account_id}...")
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Could not refresh token: {e}. Initiating new browser login...")
                creds = None

        if not creds:
            print(f"\n🌐 A browser window will now open to log into {account_name}.")
            print("👉 IMPORTANT: On the Google login screen:")
            print("   1. Choose the specific Google Account intended for this slot.")
            print("   2. If Google warns 'Google hasn't verified this app':")
            print("      Click 'Advanced' -> 'Go to Gmail Recruiter Assistant (unsafe)'.")
            print("   3. Grant permission to modify and manage Gmail messages.\n")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                # prompt='select_account consent' ensures Google always lets you pick which account to log in with
                creds = flow.run_local_server(port=0, prompt="select_account consent", access_type="offline")
            except Exception as e:
                logger.error(f"OAuth flow failed for {account_id}: {e}")
                print("\n" + "!" * 70)
                print("❌ AUTHENTICATION FAILED WITH GOOGLE")
                print("Common reasons and fixes:")
                print("1. 'Access blocked: app has not completed Google verification' or 'Error 403: access_denied'")
                print("   👉 In Google Cloud Console (https://console.cloud.google.com/):")
                print("      a. Select your project ('gmailrecruiter')")
                print("      b. Navigate to 'APIs & Services' > 'OAuth consent screen'")
                print("      c. Scroll to 'Test users' and click '+ ADD USERS'")
                print(f"      d. Add your second email address and click 'Save'")
                print("2. 'Port unavailable' or browser timeout:")
                print("   👉 Re-run: python setup_auth.py --account " + account_id)
                print("!" * 70 + "\n")
                return False, None

        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
            logger.info(f"Saved token to {token_filename}")

    # Verify service connectivity and retrieve user profile
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email_address = profile.get("emailAddress", "Unknown")
        total_messages = profile.get("messagesTotal", 0)

        print(f"\n✅ SUCCESS! Authenticated as: {email_address}")
        print(f"   Total messages in mailbox: {total_messages}")

        if existing_emails:
            for other_id, other_email in existing_emails.items():
                if other_id != account_id and other_email.lower() == email_address.lower():
                    print("\n" + "⚠️ " * 15)
                    print(f"WARNING: '{account_id}' was authenticated with the SAME email address as '{other_id}' ({email_address})!")
                    print("Both slots are currently pointing to the same mailbox.")
                    print("To fix this, re-run:")
                    print(f"   python setup_auth.py --account {account_id} --force")
                    print("and make sure to select your SECOND Google account in the browser.")
                    print("⚠️ " * 15 + "\n")

        return True, email_address
    except HttpError as err:
        logger.error(f"Failed to query Gmail API for {account_id}: {err}")
        return False, None


def main():
    parser = argparse.ArgumentParser(description="Gmail Recruiter Agent - OAuth2 Setup")
    parser.add_argument(
        "--account",
        choices=["account_1", "account_2"],
        help="Specify which account to authenticate (default: both configured accounts)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-authentication via browser even if a token already exists",
    )
    args = parser.parse_args()

    config = load_config()
    accounts = config.get("accounts", [])
    creds_file = config.get("credentials_file", "credentials.json")

    print("\n📬 Gmail Recruiter Agent - Multi-Account Authentication Setup")
    print("This script configures OAuth2 tokens for your Gmail accounts.\n")

    targets = accounts
    if args.account:
        targets = [acc for acc in accounts if acc.get("id") == args.account]
        if not targets:
            print(f"❌ Account '{args.account}' not found in config.json accounts list.")
            sys.exit(1)

    authenticated_emails = {}
    success_count = 0

    for acc in targets:
        acc_id = acc.get("id")
        acc_name = acc.get("name", acc_id)
        token_file = acc.get("token_file", f"token_{acc_id}.json")

        success, authed_email = authenticate_account(
            acc_id,
            acc_name,
            token_file,
            creds_file,
            force_reauth=args.force,
            existing_emails=authenticated_emails,
        )

        if success:
            success_count += 1
            if authed_email:
                authenticated_emails[acc_id] = authed_email
                # Update account friendly name in config if appropriate
                acc["name"] = f"{acc_id.replace('_', ' ').title()} ({authed_email})"
        else:
            print(f"⚠️ Setup for {acc_name} was not completed.")

    # Save updated friendly account names in config.json
    save_config(config)

    print("\n" + "=" * 70)
    print(f"Summary: {success_count}/{len(targets)} accounts ready.")
    for acc in accounts:
        acc_id = acc.get("id")
        tok = BASE_DIR / acc.get("token_file", "")
        status = "✅ Authenticated" if tok.exists() else "❌ Missing token"
        print(f"  • {acc.get('name', acc_id)}: {status} ({tok.name})")

    if success_count == len(targets):
        print("\n🎉 Authentication complete! You can run the agent with:")
        print("   python recruiter_agent.py")
    else:
        print("\n⚠️ Incomplete setup. If Google blocked access with Error 403 or 'Unverified app',")
        print("   ensure the second email is added as a 'Test user' in Google Cloud Console.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
