"""Automod: text rule evaluation shared by every platform.

Rules enforce the community guidelines: hate speech/slurs, NSFW terms,
invite-link advertising, Discord token leaks, caps spam and rapid repeat
spam.  Exemptions (staff roles, designated sections) are decided by the
adapter, which knows how to inspect platform roles — this service only
classifies message text.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from xesxor.core.models import Violation

#: Severe hate speech / slurs and harassment terms.  Matched on word
#: boundaries so innocent substrings (e.g. "scunthorpe") do not trip the
#: filter.  "gay"/"lesbian" are only flagged when used as a directed insult
#: ("u gay"), handled separately, so ordinary discussion is not removed.
BAD_WORDS: tuple[str, ...] = (
    # ---- racial / ethnic slurs ----
    "nigger", "nigga", "nigg", "niglet", "nigglet", "nignog", "niglet", "niguh",
    "coon", "coons", "spic", "spick", "spig", "wetback", "beaner", "chink",
    "chinky", "gook", "gooky", "kike", "kikes", "raghead", "towelhead",
    "sandnigger", "sand nigger", "jigaboo", "jiggaboo", "pickaninny", "darkie",
    "darky", "sambo", "porch monkey", "jungle bunny", "tar baby", "redskin",
    "injun", "squaw", "gypo", "gypsy", "paki", "pajeet", "pajeets", "curry muncher",
    "camel jockey", "cracka", "honky", "whitey", "gringo",
    "negro", "negroes", "nigress", "mulatto", "half-breed",
    "white trash", "white power", "master race", "gas the", "heil hitler", "hitler",
    "kkk", "ku klux", "aryan", "neo nazi", "neonazi",
    # ---- homophobic / transphobic slurs ----
    "faggot", "faggots", "fag", "fags", "fagot", "fagg", "faggit", "fagget",
    "dyke", "dykes", "tranny", "trannie", "trannies", "shemale", "shemales",
    "homo", "homos", "batty boy", "battyboy", "butt pirate", "poof", "poofter",
    "shirt lifter", "fudge packer", "pillow biter", "ladyman",
    # ---- ableist / other hate ----
    "retard", "retards", "retarded", "retardate", "mongoloid", "window licker",
    "cripple", "spastic", "spaz",
    # ---- harassment / self-harm incitement ----
    "kys", "kysl", "kill yourself", "kill urself", "neck yourself", "end yourself",
    "go die", "hope you die", "drink bleach", "commit suicide", "hang yourself",
)

#: Directed orientation insults — deliberately narrow phrases (not the bare
#: word) so "gay marriage", "gay rights", etc. are not affected.
ORIENTATION_INSULT_REGEX = re.compile(
    r"(?:"
    r"\b(?:you|u|ur|your|you'?re|yall|ya|they|he|she)\s+(?:are\s+|r\s+)?(?:so\s+|such\s+)?(?:gay|lesbo|lesbian|queer)\b"
    r"|\b(?:stop being|don'?t be|that'?s so)\s+gay\b"
    r")",
    re.IGNORECASE,
)

#: NSFW / adult / gore terms, handled with their own notice.
NSFW_WORDS: tuple[str, ...] = (
    "nude", "nudes", "naked", "nsfw", "porn", "porno", "pornography", "pornhub",
    "xvideos", "xnxx", "xhamster", "redtube", "brazzers", "xxx",
    "sex", "sexy", "sexual", "sexting", "sext", "boobs", "boob", "boobies", "tits",
    "titties", "tit", "juggs", "honkers", "pussy", "pussies", "penis", "penises",
    "dick", "dicks", "cock", "cocks", "cockhead", "cum", "cumming", "cumshot",
    "jizz", "sperm", "orgasm", "orgasms", "anal", "anus", "rectum", "vagina",
    "clit", "clitoris", "milf", "dilf", "gilf", "hentai",
    "ecchi", "rule34", "onlyfans", "blowjob", "handjob", "footjob",
    "rimjob", "masturbate", "masturbation", "fap", "fapping", "edging",
    "nipslip", "upskirt", "creampie", "gangbang", "bdsm", "bondage", "fetish",
    "nsfl", "gore", "guro", "beheading", "decapitation", "snuff", "necrophilia",
    "bestiality", "zoophile", "pedo", "pedophile", "pedophilia", "lolicon",
    "shotacon",
)

#: Common profanity, behavioural warning tier (delete + notice, no slur mark).
PROFANITY_WORDS: tuple[str, ...] = (
    "fuck", "fucks", "fucked", "fucking", "fucker", "fuckers", "motherfuck",
    "motherfucker", "motherfucking", "stfu", "wtf", "ffs", "gtfo",
    "shit", "shits", "shitty", "shitting", "bullshit", "horseshit", "apeshit",
    "bitch", "bitches", "bitchy", "bitching", "sonofabitch", "bastard",
    "bastards", "ass", "asses", "asshole", "assholes", "asswipe", "asshat",
    "dumbass", "jackass", "badass", "smartass", "dipshit", "dumbfuck",
    "slut", "sluts", "slutty", "whore", "whores", "hoe", "hoes", "skank",
    "douche", "douchebag", "douchebags", "wanker", "wankers", "prick", "pricks",
    "twat", "twats", "arse", "arsehole", "bloody", "bugger", "bollocks",
    "cunt", "cunts", "cunty", "dipstick", "scumbag", "vermin", "gaylord",
    "shut up", "screw you",
)


def _build_word_regex(words: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a word-boundary alternation for ``words``."""
    return re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(re.escape(word) for word in words) + r")(?![a-z0-9])",
        re.IGNORECASE,
    )


#: A single pre-compiled word-boundary matcher for the slur list above.
BAD_WORD_REGEX = _build_word_regex(BAD_WORDS)

#: Word-boundary matcher for NSFW terms.
NSFW_REGEX = _build_word_regex(NSFW_WORDS)

#: Word-boundary matcher for general profanity.
PROFANITY_REGEX = _build_word_regex(PROFANITY_WORDS)

INVITE_REGEX = re.compile(r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/\w+", re.IGNORECASE)
TOKEN_REGEX = re.compile(r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}")

CAPS_MIN_LENGTH = 20
CAPS_THRESHOLD = 0.75
REPEAT_WINDOW_SECONDS = 15.0
REPEAT_LIMIT = 3

HATE_SPEECH_NOTICE = (
    "Slurs, hate speech and harassment are not tolerated here "
    "(Rule 1: Be Respectful). This is your only warning."
)
INVITE_NOTICE = (
    "Advertising other servers is not allowed here "
    "(Rule 8: No Advertising). Please do not post invite links."
)
NSFW_NOTICE = (
    "NSFW, adult or gore content is not allowed here "
    "(Rule 2: Keep It Clean). Please keep the chat safe for everyone."
)
PROFANITY_NOTICE = (
    "Watch your language — repeated profanity is not welcome here "
    "(Rule 1: Be Respectful). Please keep it civil."
)
CAPS_NOTICE = "Slow down on the caps lock — that counts as spam (Rule 5: No Spam)."
REPEAT_NOTICE = "Please stop repeating the same message — that counts as spam (Rule 5: No Spam)."
TOKEN_NOTICE = (
    "Your message was removed for containing a sensitive Discord token "
    "(Rule 13: Protect Your Account). Please secure your account immediately!"
)


class AutomodService:
    """Stateless text classification plus a small repeat-spam cache."""

    def __init__(self) -> None:
        # (user_id, scope_id) -> list of {'content', 'time'}
        self._recent: dict[tuple[str, str], list[dict]] = {}

    def check(self, user_id: str, scope_id: str, text: str) -> Optional[Violation]:
        """Classify one message.

        Args:
            user_id: Author id.
            scope_id: Guild/chat scope (keys the repeat-spam cache).
            text: Raw message text.

        Returns:
            A :class:`Violation` when a rule matched, otherwise ``None``.
        """
        if BAD_WORD_REGEX.search(text) or ORIENTATION_INSULT_REGEX.search(text):
            return Violation(
                rule="hate_speech", action="delete_notify", notice=HATE_SPEECH_NOTICE
            )

        if NSFW_REGEX.search(text):
            return Violation(rule="nsfw_content", action="delete_notify", notice=NSFW_NOTICE)

        if PROFANITY_REGEX.search(text):
            return Violation(rule="profanity", action="delete_notify", notice=PROFANITY_NOTICE)

        if INVITE_REGEX.search(text):
            return Violation(rule="invite_link", action="delete_notify", notice=INVITE_NOTICE)

        if TOKEN_REGEX.search(text):
            return Violation(rule="token_leak", action="delete_notify", notice=TOKEN_NOTICE)

        if len(text) > CAPS_MIN_LENGTH:
            uppercase = sum(1 for char in text if char.isupper())
            if uppercase / len(text) > CAPS_THRESHOLD:
                return Violation(rule="caps_spam", action="delete_notify", notice=CAPS_NOTICE)

        if self._is_repeat_spam(user_id, scope_id, text):
            return Violation(rule="repeat_spam", action="delete_notify", notice=REPEAT_NOTICE)

        return None

    def _is_repeat_spam(self, user_id: str, scope_id: str, text: str) -> bool:
        """Detect the same message posted ``REPEAT_LIMIT`` times in the window."""
        key = (user_id, scope_id)
        now = time.time()
        history = [
            entry
            for entry in self._recent.get(key, [])
            if now - entry["time"] < REPEAT_WINDOW_SECONDS
        ]
        matching = sum(1 for entry in history if entry["content"] == text)
        history.append({"content": text, "time": now})
        self._recent[key] = history[-20:]
        return matching + 1 >= REPEAT_LIMIT

    def exempt_placeholders(self) -> tuple[str, ...]:
        """Category/channel name fragments exempt from automod (informational)."""
        return ("team",)
