"""AI-generated quizzes with duplicate protection and an offline fallback.

Flow:
1. ``start`` checks the daily limit and the entry fee (10 Coins).
2. The AI generates one question; a content hash prevents ever repeating a
   question to the same user (two generation attempts on collision).
3. If the AI is down or malformed, a curated fallback pool is used so the
   feature never hard-fails.
4. The question is held in memory until answered or the 30s window closes.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Optional

from xesxor.core.database import Database
from xesxor.core.errors import CommandError
from xesxor.core.models import QuizQuestion, QuizResult
from xesxor.core.services.ai import AIService
from xesxor.core.services.economy import EconomyService
from xesxor.utils.helpers import question_hash, utc_day_start_ts

logger = logging.getLogger(__name__)

QUIZ_FEE = 10.0
QUIZ_REWARD = 20.0
DAILY_LIMIT = 10
ANSWER_WINDOW_SECONDS = 30.0

CATEGORIES: dict[str, str] = {
    "redteam": "Red Team",
    "blueteam": "Blue Team",
    "coding": "Coding",
    "it": "IT Fundamentals",
}


@dataclass
class _PendingQuiz:
    """In-memory state for one unanswered quiz session."""

    question: QuizQuestion
    deadline: float


class QuizService:
    """Generate, deliver and score quizzes across all platforms."""

    def __init__(self, db: Database, ai: AIService, economy: EconomyService) -> None:
        self._db = db
        self._ai = ai
        self._economy = economy
        self._pending: dict[tuple[str, str], _PendingQuiz] = {}

    # ------------------------------------------------------------------
    async def start(self, user_id: str, scope_id: str, category_key: str) -> QuizQuestion:
        """Begin a quiz session for *user_id*.

        Args:
            user_id: The answering user.
            scope_id: Guild/chat scope for limits and balances.
            category_key: One of :data:`CATEGORIES` keys.

        Returns:
            The question to display.

        Raises:
            CommandError: daily limit reached or not enough Coins.
        """
        category = CATEGORIES.get(category_key, "Coding")
        self._economy.ensure_user(user_id, scope_id)

        now_ts = time.time()
        day_start = utc_day_start_ts()
        row = self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM quiz_history "
            "WHERE user_id = ? AND scope_id = ? AND timestamp >= ?",
            (user_id, scope_id, day_start),
        )
        used_today = int(row["cnt"]) if row else 0
        if used_today >= DAILY_LIMIT:
            reset_in = int(day_start + 86_400 - now_ts)
            hours, minutes = divmod(max(reset_in, 0) // 60, 60)
            raise CommandError(
                f"**Daily quiz limit reached — {DAILY_LIMIT}/{DAILY_LIMIT} used today.**\n"
                f"Resets in **{hours}h {minutes}m** at midnight UTC. Come back tomorrow!"
            )

        balance_row = self._db.fetchone(
            "SELECT coins FROM users WHERE user_id = ? AND scope_id = ?",
            (user_id, scope_id),
        )
        balance = balance_row["coins"] if balance_row else 0.0
        if balance < QUIZ_FEE:
            raise CommandError(f"You need at least **{QUIZ_FEE:.0f} Coins** to attempt this quiz.")

        self._db.execute(
            "UPDATE users SET coins = coins - ? WHERE user_id = ? AND scope_id = ?",
            (QUIZ_FEE, user_id, scope_id),
        )

        question = await self._generate(user_id, scope_id, category, used_today + 1)

        self._db.execute(
            "INSERT INTO quiz_history "
            "(user_id, scope_id, question_hash, question, category, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, scope_id, question_hash) DO NOTHING",
            (user_id, scope_id, question_hash(question.question),
             question.question[:500], category, now_ts),
        )
        self._pending[(user_id, scope_id)] = _PendingQuiz(
            question=question, deadline=time.monotonic() + ANSWER_WINDOW_SECONDS
        )
        return question

    async def answer(self, user_id: str, scope_id: str, choice: str) -> QuizResult:
        """Score the user's answer.

        Raises:
            CommandError: no active quiz, expired window, or someone else's
                session.
        """
        key = (user_id, scope_id)
        pending = self._pending.get(key)
        if pending is None:
            raise CommandError("You have no active quiz. Start one with `/quiz-coding`.")
        if time.monotonic() > pending.deadline:
            self._pending.pop(key, None)
            raise CommandError("⏱️ Time's up! The quiz window (30s) has closed.")

        self._pending.pop(key, None)
        correct = choice.upper() == pending.question.correct
        if correct:
            self._economy.add_coins(user_id, scope_id, QUIZ_REWARD)
        return QuizResult(
            correct=correct,
            correct_option=pending.question.correct,
            reward=QUIZ_REWARD if correct else 0.0,
            explanation=pending.question.explanation,
        )

    def is_expired(self, user_id: str, scope_id: str) -> bool:
        """Return True when a pending quiz exists but its window has closed."""
        pending = self._pending.get((user_id, scope_id))
        return pending is not None and time.monotonic() > pending.deadline

    # ------------------------------------------------------------------
    async def _generate(
        self, user_id: str, scope_id: str, category: str, used_today: int
    ) -> QuizQuestion:
        """Produce a question, preferring the AI and falling back to a pool."""
        seen = {
            row["question_hash"]
            for row in self._db.fetchall(
                "SELECT question_hash FROM quiz_history WHERE user_id = ? AND scope_id = ?",
                (user_id, scope_id),
            )
        }
        recent = [
            row["question"]
            for row in self._db.fetchall(
                "SELECT question FROM quiz_history WHERE user_id = ? AND scope_id = ? "
                "ORDER BY timestamp DESC LIMIT 20",
                (user_id, scope_id),
            )
        ]

        data = await self._generate_ai(category, recent)
        if data is not None:
            candidate_hash = question_hash(str(data["question"]))
            if candidate_hash not in seen and self._valid_options(data["options"]):
                return self._build_question(category, data, used_today)

        return self._fallback_question(category, seen, used_today)

    async def _generate_ai(self, category: str, recent: list[str]) -> Optional[dict]:
        """Ask the AI for one question; returns parsed JSON or ``None``."""
        if not self._ai.configured:
            return None
        pool = _TOPIC_POOL.get(category, _TOPIC_POOL["Coding"])
        subtopics = " + ".join(random.sample(pool, k=min(2, len(pool))))
        exclusion = ""
        if recent:
            listed = "\n".join(f"- {q[:90]}" for q in recent)
            exclusion = (
                "PREVIOUSLY ASKED — DO NOT REPEAT, PARAPHRASE OR VARY ANY OF THESE:\n"
                f"{listed}\n"
            )
        prompt = (
            f"Generate ONE intermediate {category} quiz question. Focus on: {subtopics}.\n"
            f"{exclusion}"
            "STRICT RULES:\n"
            "- Test APPLIED knowledge through a mini-scenario, log line, command or "
            "1-2 line code snippet — not simple recall.\n"
            "- The ENTIRE question must be under 55 words, solvable in ~30 seconds.\n"
            "- Exactly 4 options A-D, each at most 12 words, exactly one correct. "
            "Make at least one distractor a classic trap (off-by-one, plausible wrong port/flag).\n"
            "- Never invent flags, CVEs, Event IDs, or invalid syntax.\n"
            'Format strictly as JSON: {"question": "...", "options": ["A. ...", "B. ...", '
            '"C. ...", "D. ..."], "correct": "A", "explanation": "one sentence, max 25 words"}'
        )
        raw = await self._ai.ask(prompt, platform_context="quiz game")
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group(0) if match else raw)
            if (
                data.get("question")
                and isinstance(data.get("options"), list)
                and len(data["options"]) == 4
                and data.get("correct") in ("A", "B", "C", "D")
            ):
                return data
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning("Quiz AI parse failed: %s", exc)
        return None

    @staticmethod
    def _valid_options(options: list) -> bool:
        """Reject duplicate option text after stripping the A./B. prefixes."""
        normalized = [
            re.sub(r"^[A-D]\.\s*", "", str(opt), flags=re.IGNORECASE).strip().lower()
            for opt in options
        ]
        return len(set(normalized)) == 4

    def _build_question(self, category: str, data: dict, used_today: int) -> QuizQuestion:
        options = tuple(
            re.sub(r"^[A-D]\.\s*", "", str(opt), flags=re.IGNORECASE).strip()
            for opt in data["options"]
        )
        return QuizQuestion(
            category=category,
            question=str(data["question"]).strip(),
            options=options,
            correct=str(data["correct"]).upper(),
            explanation=str(data.get("explanation", ""))[:300],
            fee=QUIZ_FEE,
            reward=QUIZ_REWARD,
            used_today=used_today,
            daily_limit=DAILY_LIMIT,
        )

    def _fallback_question(
        self, category: str, seen: set[str], used_today: int
    ) -> QuizQuestion:
        """Pick an unseen curated question, or a salted repeat as last resort."""
        pool = _FALLBACK_POOL.get(category, _FALLBACK_POOL["Coding"])
        unseen = [entry for entry in pool if question_hash(entry[0]) not in seen]
        if unseen:
            text, options, correct, explanation = random.choice(unseen)
        else:
            text, options, correct, explanation = random.choice(pool)
            text = f"{text} [#{random.randint(100000, 999999)}]"
        return QuizQuestion(
            category=category,
            question=text,
            options=tuple(options),
            correct=correct,
            explanation=explanation,
            fee=QUIZ_FEE,
            reward=QUIZ_REWARD,
            used_today=used_today,
            daily_limit=DAILY_LIMIT,
        )


# ----------------------------------------------------------------------
# Curated offline content (port of the v1 fallback pool).
# ----------------------------------------------------------------------

_TOPIC_POOL: dict[str, list[str]] = {
    "Red Team": [
        "Nmap scan types & flags", "Hydra / Hashcat / John usage", "Burp Suite & HTTP interception",
        "SQL injection payloads & bypass", "XSS (stored/reflected/DOM) & CSP", "Metasploit modules & payloads",
        "Linux privilege escalation (SUID, sudo, cron)", "Windows AD attacks (Kerberoasting, Pass-the-Hash)",
        "Reverse shells & payload encoding", "Network pivoting & tunneling",
    ],
    "Blue Team": [
        "Windows Event IDs & Sysmon", "Linux auth logs & journalctl", "SIEM correlation & detection rules",
        "IDS/IPS signatures (Snort/Suricata)", "MITRE ATT&CK mapping", "Firewall / WAF rule analysis",
        "Incident response phases & forensics", "Threat hunting hypotheses & IOAs", "SOC triage & alert severity",
        "Memory forensics & registry artifacts",
    ],
    "Coding": [
        "HTML semantics & forms", "CSS selectors, box model, flexbox/grid",
        "JavaScript DOM, closures, promises & async/await", "PHP superglobals, sessions & prepared statements",
        "SQL queries, JOINs, indexing & normalization", "Python slicing, comprehensions, decorators & OOP",
        "Java OOP, collections, exceptions & JVM basics", "HTTP/REST, Git, Big-O, regex & data structures",
    ],
    "IT Fundamentals": [
        "DNS records & resolution order", "DHCP lease & IP allocation", "OSI vs TCP/IP layer mapping",
        "Subnetting, CIDR & netmask math", "Linux file permissions & chmod", "Ports, processes & netstat/lsof",
        "RAID levels & disk redundancy", "Virtualization vs containers", "TLS handshake & certificate chain",
        "Backup types & restore strategy",
    ],
}

_FALLBACK_POOL: dict[str, list[tuple[str, list[str], str, str]]] = {
    "Red Team": [
        ("You run `hydra -l admin -P rockyou.txt -f ssh://10.10.0.5`. What does `-f` do?",
         ["Exit after first valid password", "Force verbose output", "Randomize hosts", "Use first 10 passwords only"],
         "A", "`-f` exits Hydra on first found credential; without it Hydra tries the whole list."),
        ("Nmap: `nmap -sS -p 80 10.0.0.5` vs `nmap -sT -p 80 10.0.0.5`. Which statement is true?",
         ["Both need root", "-sS completes full TCP handshake", "-sT uses OS connect() and completes handshake", "-sS sends UDP"],
         "C", "-sT is the full connect scan via connect(); -sS is SYN-half-open and needs raw sockets."),
        ("Payload ` ' OR 1=1 -- ` in `SELECT * FROM users WHERE id='INPUT'` — result?",
         ["Syntax error", "Returns all rows", "Returns no rows", "Deletes table"],
         "B", "The OR 1=1 makes the WHERE always true, returning every row."),
        ("`chmod 754 file.sh` — what can group do?",
         ["rwx", "r-x", "r--", "--x"],
         "B", "754 is rwxr-xr--: user rwx, group r-x, other r--."),
    ],
    "Blue Team": [
        ("Windows log: Event 4625 at 02:11, then 4624 from same IP. What occurred?",
         ["Successful logon then failure", "Failed logon then success (possible brute-force)", "User created", "Process spawned"],
         "B", "4625 is failed logon, 4624 is success — classic brute-force success pattern."),
        ("Which Suricata rule keyword matches HTTP URI content?",
         ["http_uri", "http_header", "tls_sni", "dns_query"],
         "A", "http_uri matches the URI; http_header matches headers, tls_sni is TLS."),
        ("SIEM correlation: 5 failed logons then success from same src in 2 min — severity?",
         ["Informational", "Medium — possible brute-force success", "Low — ignore", "Critical — ransomware"],
         "B", "Multiple failures followed by success is a high-confidence brute-force indicator."),
        ("`journalctl -u ssh.service --since today | grep Failed` shows what?",
         ["Successful SSH logins", "Failed SSH attempts", "Firewall blocks", "DHCP leases"],
         "B", "That journalctl filters the SSH unit for 'Failed' lines."),
    ],
    "Coding": [
        ("HTML: `<form><input type='email' required></form>` submitted empty — what happens?",
         ["Submits anyway", "Browser blocks submit, shows validation", "Sends null to server", "Converts to text field"],
         "B", "`required` triggers native browser validation and blocks empty submit."),
        ("CSS: `div > p {color:red}` with `<div><section><p>Hi</p></section></div>` — is <p> red?",
         ["Yes, all p inside div", "No, only direct child p", "Yes, only first p", "No, selector invalid"],
         "B", "`>` is child combinator — only direct children, not nested inside section."),
        ("JS: `console.log(typeof null)` outputs what?",
         ['"null"', '"object"', '"undefined"', '"number"'],
         "B", '`typeof null` is famously "object" — a legacy JS bug kept for compatibility.'),
        ("PHP: URL `/page.php?id=0` and `if(isset($_GET[\'id\'])) echo \"yes\";` — prints?",
         ["Nothing", "yes", "0", "Error"],
         "B", '`isset` is true even for "0"; `empty` would be false for "0".'),
        ("SQL: `SELECT * FROM a LEFT JOIN b ON a.id=b.a_id` — which rows are kept?",
         ["Only matching rows", "All rows from a, matched b or NULL", "All rows from b only", "No rows"],
         "B", "LEFT JOIN keeps every row from left table a, filling b with NULL when no match."),
        ("Python: `print([1,2,3,4][1:3].__len__())` outputs?",
         ["1", "2", "3", "4"],
         "B", "Slice [1:3] is [2,3], length 2."),
        ("Java: `System.out.println(3/2);` prints?",
         ["1", "1.5", "2", "Compile error"],
         "A", "Both operands are int, so integer division truncates to 1; 3/2.0 would be 1.5."),
        ("Java: `String a=\"hi\"; String b=new String(\"hi\");` `a==b` vs `a.equals(b)`?",
         ["Both true", "Both false", "== false, equals true", "== true, equals false"],
         "C", "== compares references, `.equals()` compares content."),
    ],
    "IT Fundamentals": [
        ("Network 192.168.1.0/28 — how many usable host IPs?",
         ["6", "14", "16", "30"],
         "B", "/28 is 255.255.255.240 — 16 total, 14 usable (minus network/broadcast)."),
        ("Which record maps a domain to an IPv6 address?",
         ["A", "AAAA", "CNAME", "MX"],
         "B", "AAAA is IPv6; A is IPv4, CNAME is alias, MX is mail."),
        ("`dig +short example.com` returns empty, `dig @8.8.8.8 example.com` works — cause?",
         ["Local DNS misconfigured", "Domain expired", "Firewall blocks port 80", "DHCP failure"],
         "A", "System resolver fails but direct query to 8.8.8.8 works, so local DNS is broken."),
        ("RAID 1 with 2×1TB disks — usable capacity?",
         ["2 TB", "1 TB", "500 GB", "0 TB"],
         "B", "RAID 1 mirrors: usable is size of one disk."),
    ],
}
