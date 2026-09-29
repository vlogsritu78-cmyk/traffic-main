"""Bilingual (Tamil + English) welcome copy. The two body blocks are editable from
the admin panel; the frame around them stays fixed."""

import html

DIVIDER = "━━━━━━━━━━━━━━━━━━"

DEFAULT_TA = (
    "நீங்கள் சரியான இடத்தில் இருக்கிறீர்கள்.\n"
    "கீழே உள்ள பட்டனை அழுத்தி எங்கள்\n"
    "அதிகாரப்பூர்வ கணக்குடன் நேரடியாக\n"
    "உரையாடுங்கள். 👇"
)

DEFAULT_EN = (
    "You're in the right place.\n"
    "Tap the button below to chat directly\n"
    "with our Official Account. 👇"
)


def welcome_text(username: str, tamil_body: str | None = None, english_body: str | None = None) -> str:
    ta = html.escape((tamil_body or DEFAULT_TA).strip())
    en = html.escape((english_body or DEFAULT_EN).strip())
    return (
        "✨ <b>வணக்கம்</b>  ·  <b>Welcome</b> ✨\n"
        f"<code>{DIVIDER}</code>\n"
        "\n"
        "🟡 <b>தமிழ்</b>\n"
        f"{ta}\n"
        "\n"
        f"<code>{DIVIDER}</code>\n"
        "\n"
        "🔵 <b>English</b>\n"
        f"{en}\n"
        "\n"
        f"<code>{DIVIDER}</code>\n"
        "\n"
        "⚡ விரைவான பதில்  ·  Fast reply\n"
        "🔒 முழுமையான தனிமை  ·  100% private\n"
        "🕐 எப்போதும் கிடைக்கும்  ·  Available 24/7\n"
        "\n"
        f"👤 <b>{html.escape(username)}</b>"
    )


BOT_DESCRIPTION = (
    "வணக்கம்! 👋 அதிகாரப்பூர்வ கணக்குடன் நேரடியாக உரையாட START அழுத்துங்கள்.\n"
    "Welcome! 👋 Tap START to chat directly with our Official Account."
)

BOT_SHORT_DESCRIPTION = "⚡ Instantly connect with our Official Telegram Account · தமிழ் / English"
