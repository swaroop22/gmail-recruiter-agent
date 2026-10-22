# 📬 Gmail Recruiter Agent (Multi-Account)

An automated Python background service that monitors **two separate Gmail accounts** simultaneously every 5 minutes for incoming recruiter emails and sends polite, professional, threaded replies or creates Gmail drafts.

---

## 🌟 Key Features

- **Multi-Account OAuth2 Architecture**: Authenticates and maintains independent tokens (`token_account1.json` & `token_account2.json`) from a single OAuth client or multiple accounts without collisions.
- **Continuous 5-Minute Scheduler**: Uses Python's `schedule` library to poll both accounts periodically with an immediate initial scan on startup.
- **Intelligent Recruiter Parsing**:
  - Automatically isolates recruiter names from `From` headers (e.g. `"Sarah Jenkins <sarah@recruiting.com>"` ➔ `"Sarah"`).
  - Ignores automated alerts, job board newsletters, and bots (e.g., `jobalerts-noreply@linkedin.com`, `no-reply`, etc.).
- **Threaded & Standard-Compliant Replies**:
  - Maintains Gmail conversation threads by setting `threadId`, `In-Reply-To`, `References`, and `Re: <Subject>` headers.
  - Automatically creates and applies a custom Gmail label (`Recruiter-Replied`) to easily distinguish handled conversations in Gmail web and mobile apps.
- **Action Modes**:
  - `reply`: Automatically sends the response email directly.
  - `draft`: Creates a draft response in the Gmail thread so you can review before sending manually.
- **Dual Logging & State Persistence**:
  - Console and rotating file logging (`agent.log`).
  - Atomic JSON state tracking (`processed_emails.json`) to guarantee no recruiter receives duplicate replies.
- **Fault-Tolerant Loop**: Recovers smoothly from network drops, rate limits (HTTP 429), or temporary Google API outages.

---

## 📁 Project Directory Structure

```text
gmail-recruiter-agent/
├── config.json             # Configuration (candidate profile, queries, timing, accounts)
├── recruiter_agent.py      # Main background monitoring daemon
├── setup_auth.py           # Interactive OAuth setup tool for Account 1 & 2
├── requirements.txt        # Python package dependencies
├── credentials.json        # Google Cloud OAuth client credentials (downloaded by you)
├── token_account1.json     # Generated token for Account 1 (gitignored)
├── token_account2.json     # Generated token for Account 2 (gitignored)
├── processed_emails.json   # Processed email state file (gitignored)
├── agent.log               # Detailed log file (gitignored)
└── .gitignore
```

---

## ⚙️ Step 1: Google Cloud Console Setup (`credentials.json`)

To allow the script to connect to Gmail via OAuth2:

1. **Go to Google Cloud Console**:
   - Open [Google Cloud Console](https://console.cloud.google.com/).
   - Click the project dropdown at the top and click **"New Project"** (e.g., `gmail-recruiter-agent`).

2. **Enable Gmail API**:
   - Go to **APIs & Services** > **Library** from the left navigation menu.
   - Search for **"Gmail API"**, click on it, and click **"Enable"**.

3. **Configure OAuth Consent Screen**:
   - Navigate to **APIs & Services** > **OAuth consent screen**.
   - Choose **External** and click **Create**.
   - Fill in the required fields:
     - **App name**: `Gmail Recruiter Assistant`
     - **User support email**: Your primary Gmail.
     - **Developer contact information**: Your primary Gmail.
   - Click **Save and Continue**.
   - Under **Scopes**, click **Add or Remove Scopes**:
     - Find and check: `https://www.googleapis.com/auth/gmail.modify` (Allows reading messages, composing drafts, sending replies, and modifying labels).
     - Click **Update** and then **Save and Continue**.
   - Under **Test Users** (**CRITICAL STEP**):
     - Click **Add Users**.
     - Add the email address of **Account 1** AND the email address of **Account 2**.
     - Click **Save and Continue**.

4. **Create OAuth Client ID Credentials**:
   - Navigate to **APIs & Services** > **Credentials**.
   - Click **+ Create Credentials** at the top and select **OAuth client ID**.
   - In **Application type**, select **Desktop app**.
   - Name it `Recruiter Agent Desktop Client` and click **Create**.
   - In the pop-up modal, click **Download JSON**.
   - Rename this downloaded file to `credentials.json` and move it directly into this project directory:
     ```bash
     mv ~/Downloads/client_secret_*.json ./credentials.json
     ```

---

## 🚀 Step 2: Installation & Virtual Environment

Open your terminal, navigate to the project directory, and set up a virtual environment:

```bash
cd /Users/krishnajyothiswarooppothamsetti/Desktop/Gemini Projects/gmail-recruiter-agent

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

---

## 🔑 Step 3: Authenticate Both Accounts

Run the interactive authorization assistant:

```bash
python setup_auth.py
```

1. **For Account 1**:
   - Your web browser will open automatically.
   - Choose your **Account 1** Google account.
   - Click **Continue** (or "Advanced" > "Go to Gmail Recruiter Assistant (unsafe)" if you see Google's unverified app warning).
   - Click **Allow** on the permissions screen.
   - The script will save `token_account1.json` and verify mailbox connectivity.
2. **For Account 2**:
   - The script will automatically trigger the browser again for Account 2.
   - Choose your **Account 2** Google account.
   - Grant permissions.
   - The script will save `token_account2.json`.

---

## 🛠️ Step 4: Configuration (`config.json`)

Inspect and tailor `config.json` to your preferences:

```json
{
  "check_interval_minutes": 5,
  "action_mode": "reply",           // "reply" (auto-send) or "draft" (review first)
  "mark_as_read_after_reply": false,
  "apply_gmail_label": true,
  "gmail_label_name": "Recruiter-Replied",
  "candidate": {
    "name": "Krishna",
    "role": "Senior Java Full Stack Developer",
    "phone": "+1 (555) 019-2834",
    "linkedin_url": "https://linkedin.com/in/yourprofile",
    "portfolio_url": "https://github.com/yourprofile",
    "custom_message": "Thank you for reaching out regarding the opportunity. I am currently evaluating my next career move as a Senior Java Full Stack Developer. Please let me know the best time to schedule an initial discussion or share more details (job description, compensation range, and work arrangement) about the role."
  }
}
```

> **Tip**: If you want to test safely first without sending real emails, set `"action_mode": "draft"`. The script will generate complete drafts inside the recruiter's thread in Gmail so you can preview them!

---

## 🏃 Step 5: Running the Background Service

### Option A: Run Directly in Terminal
```bash
python recruiter_agent.py
```

### Option B: Run in Background (nohup)
```bash
nohup python recruiter_agent.py > /dev/null 2>&1 &
echo $! > agent.pid
```
To monitor the background logs:
```bash
tail -f agent.log
```
To stop the background agent:
```bash
kill $(cat agent.pid) && rm agent.pid
```

### Option C: macOS LaunchAgent (Runs on system startup)
Create a file at `~/Library/LaunchAgents/com.recruiter.agent.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.recruiter.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/krishnajyothiswarooppothamsetti/Desktop/Gemini Projects/gmail-recruiter-agent/venv/bin/python</string>
        <string>/Users/krishnajyothiswarooppothamsetti/Desktop/Gemini Projects/gmail-recruiter-agent/recruiter_agent.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/krishnajyothiswarooppothamsetti/Desktop/Gemini Projects/gmail-recruiter-agent</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```
Load and start it with:
```bash
launchctl load ~/Library/LaunchAgents/com.recruiter.agent.plist
```

---

## 🧪 Verification & Unit Testing

To test unit logic (name extraction, bot filtering, response generation, state saving):
```bash
python -m unittest test_agent.py
```
