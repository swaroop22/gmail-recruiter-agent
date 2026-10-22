#!/usr/bin/env python3
"""
Unit tests for Gmail Recruiter Agent logic.
Validates name extraction, bot filtering, reply message generation, and state management.
"""

import unittest
import json
import base64
from pathlib import Path
import tempfile

from recruiter_agent import (
    extract_recruiter_first_name,
    is_automated_or_bot,
    is_interview_invite,
    generate_response_text,
    build_reply_email,
    load_state,
    save_state
)


class TestRecruiterAgent(unittest.TestCase):

    def test_extract_recruiter_first_name(self):
        cases = [
            ("Sarah Jenkins <sarah.jenkins@techrecruiting.com>", "Sarah"),
            ("\"Doe, John\" <john.doe@hiringfirm.com>", "John"),
            ("Michael <michael@startuphr.io>", "Michael"),
            ("alex.smith@consulting.com", "Alex"),
            ("<unknown@generic.com>", "there"),
            ("no-reply@company.com", "there"),
        ]
        for header, expected in cases:
            with self.subTest(header=header):
                result = extract_recruiter_first_name(header)
                self.assertEqual(result, expected)

    def test_is_automated_or_bot(self):
        ignored = ["no-reply", "noreply", "jobalerts-noreply@linkedin.com"]

        # Bots, LinkedIn, Dice, ZipRecruiter, Indeed, Monster exclusions
        self.assertTrue(is_automated_or_bot("jobalerts-noreply@linkedin.com", "New jobs for you", ignored))
        self.assertTrue(is_automated_or_bot("recruiter@linkedin.com", "Opportunity at Tech Corp", []))
        self.assertTrue(is_automated_or_bot("Jane Doe via LinkedIn <notifications@linkedin.com>", "Message from Jane", []))
        self.assertTrue(is_automated_or_bot("recruiter@agency.com", "InMail: Senior Java Role", []))
        self.assertTrue(is_automated_or_bot("notifications@dice.com", "Matches for Java Developer", []))
        self.assertTrue(is_automated_or_bot("jobs@ziprecruiter.com", "Top hiring companies", []))
        self.assertTrue(is_automated_or_bot("alert@indeed.com", "Java developer opening", []))
        self.assertTrue(is_automated_or_bot("monster@notifications.monster.com", "Job alert for Java", []))
        self.assertTrue(is_automated_or_bot("recruiter@agency.com", "ZipRecruiter application notice", []))
        self.assertTrue(is_automated_or_bot("support@greatrecruiters.com", "Review your recent recruiter experience", []))
        self.assertTrue(is_automated_or_bot("no-reply@glassdoor.com", "Job alert", ignored))
        self.assertTrue(is_automated_or_bot("system@company.com", "Weekly Digest of Opportunities", ignored))

        # Real recruiters
        self.assertFalse(is_automated_or_bot("laura@apexsystems.com", "Senior Java Role at FinTech", ignored))
        self.assertFalse(is_automated_or_bot("dave.hiring@google.com", "Interview Invitation", ignored))

    def test_is_interview_invite(self):
        # Explicit interview invitations and schedules
        self.assertTrue(is_interview_invite("Interview Invitation: Senior Java Developer", ""))
        self.assertTrue(is_interview_invite("Invitation to Interview with Amazon", ""))
        self.assertTrue(is_interview_invite("Technical Interview - Round 1", ""))
        self.assertTrue(is_interview_invite("Final Round Interview Schedule", ""))
        self.assertTrue(is_interview_invite("Phone Interview Next Steps", ""))
        self.assertTrue(is_interview_invite("Coding Assessment: HackerRank Challenge", ""))
        self.assertTrue(is_interview_invite("Invitation: Google Meet Interview", ""))
        self.assertTrue(is_interview_invite("Next Steps", snippet="We would like to invite you to an interview with our VP."))
        self.assertTrue(is_interview_invite("Meeting Schedule", from_header="scheduling@calendly.com"))
        self.assertTrue(is_interview_invite("Interview Confirmation", from_header="recruiting@greenhouse-mail.io"))

        # Real initial recruiter outreach (MUST NOT be skipped)
        self.assertFalse(is_interview_invite("Senior Java Full Stack Role at Capital One", "I came across your profile and would love to connect.", from_header="recruiter@capitalone.com"))
        self.assertFalse(is_interview_invite("Job Opportunity: Lead Java Engineer", "Are you open to exploring new roles?", from_header="sarah@techpartners.com"))

    def test_generate_response_text(self):
        config = {
            "candidate": {
                "name": "Krishna",
                "role": "Senior Java Full Stack Developer",
                "phone": "+1 555-0100",
                "linkedin_url": "https://linkedin.com/in/demo",
                "custom_message": "Thank you for reaching out regarding the opportunity. I am currently evaluating my next career move as a Senior Java Full Stack Developer."
            }
        }
        text = generate_response_text("Rachel", config)
        self.assertIn("Hi Rachel,", text)
        self.assertIn("Senior Java Full Stack Developer", text)
        self.assertIn("Krishna", text)
        self.assertIn("Phone: +1 555-0100", text)

    def test_build_reply_email(self):
        import email
        payload = build_reply_email(
            to_email="recruiter@agency.com",
            subject="Senior Java Full Stack Opportunity",
            body_text="Hi Recruiter, sounds great!",
            thread_id="thread_12345",
            message_id_header="<msg_001@mail.gmail.com>",
            references_header="<ref_000@mail.gmail.com>"
        )

        self.assertEqual(payload.get("threadId"), "thread_12345")
        raw_bytes = base64.urlsafe_b64decode(payload.get("raw"))
        parsed_email = email.message_from_bytes(raw_bytes)

        self.assertEqual(parsed_email["To"], "recruiter@agency.com")
        self.assertEqual(parsed_email["Subject"], "Re: Senior Java Full Stack Opportunity")
        self.assertEqual(parsed_email["In-Reply-To"], "<msg_001@mail.gmail.com>")
        self.assertEqual(parsed_email["References"], "<ref_000@mail.gmail.com> <msg_001@mail.gmail.com>")

        # Walk parts to verify body text
        body_found = False
        for part in parsed_email.walk():
            if part.get_content_type() == "text/plain":
                payload_str = part.get_payload(decode=True).decode("utf-8")
                if "Hi Recruiter, sounds great!" in payload_str:
                    body_found = True
        self.assertTrue(body_found)

    def test_state_persistence(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            state = {"processed_message_ids": {"msg_101": {"status": "replied"}}, "processed_thread_ids": {}}
            save_state(tmp_path, state)

            loaded = load_state(tmp_path)
            self.assertIn("msg_101", loaded["processed_message_ids"])
            self.assertEqual(loaded["processed_message_ids"]["msg_101"]["status"], "replied")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
