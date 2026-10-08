import os
import json
import base64
import re
import sqlite3
import hashlib
import secrets
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI


# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

st.set_page_config(
    page_title="AI Stylist",
    page_icon="👕",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = os.path.join(DATA_DIR, "ai_stylist.db")

MODEL = "gpt-5.6-sol"

PRICES = {
    "analysis": 20,
    "improve": 10,
    "wardrobe_outfit": 50,
}

STYLES = [
    "Indie Sleaze",
    "Drip / Streetwear",
    "Old Money",
    "Y2K",
    "Minimalism",
]


# =========================================================
# API
# =========================================================

api_key = os.getenv("AITUNNEL_API_KEY")

if not api_key:
    st.error("❌ API-ключ AITUNNEL_API_KEY не найден.")
    st.info(
        "Добавь AITUNNEL_API_KEY в Environment Variables "
        "в Coolify и сделай Redeploy."
    )
    st.stop()

client = OpenAI(
    api_key=api_key,
    base_url="https://api.aitunnel.ru/v1",
    timeout=600.0,
    max_retries=2,
)


# =========================================================
# CSS
# =========================================================

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    .block-container {
        max-width: 1180px;
        padding-top: 1.5rem;
        padding-bottom: 3rem;
    }

    .hero {
        padding: 28px 30px;
        border-radius: 28px;
        background: linear-gradient(
            135deg,
            rgba(245,245,245,.98),
            rgba(255,255,255,.98)
        );
        border: 1px solid rgba(0,0,0,.07);
        margin-bottom: 22px;
    }

    .hero-title {
        font-size: 42px;
        font-weight: 850;
        letter-spacing: -1.5px;
        margin: 0;
    }

    .hero-subtitle {
        color: #777;
        font-size: 17px;
        margin-top: 6px;
    }

    .card {
        padding: 22px;
        border-radius: 22px;
        border: 1px solid rgba(0,0,0,.08);
        background: rgba(255,255,255,.75);
        margin-bottom: 16px;
    }

    .price {
        font-size: 25px;
        font-weight: 800;
        margin: 6px 0;
    }

    .muted {
        color: #777;
    }

    .balance {
        font-size: 34px;
        font-weight: 850;
        margin-top: 3px;
    }

    .login-wrap {
        max-width: 560px;
        margin: 55px auto;
    }

    .login-title {
        text-align: center;
        font-size: 44px;
        font-weight: 850;
        letter-spacing: -1.5px;
    }

    .login-subtitle {
        text-align: center;
        color: #777;
        margin-bottom: 28px;
    }

    div[data-testid="stTabs"] button {
        font-size: 16px;
        font-weight: 700;
    }

    .section-title {
        font-size: 30px;
        font-weight: 820;
        margin-top: 5px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# DATABASE
# =========================================================

def get_db():
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def column_exists(con, table, column):
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_database():
    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0,
            free_analysis_used INTEGER NOT NULL DEFAULT 0,
            free_wardrobe_outfit_used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS outfit_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            style TEXT NOT NULL,
            analysis TEXT NOT NULL,
            improved_result TEXT,
            image BLOB,
            image_name TEXT,
            image_type TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_outfits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            style TEXT,
            items_json TEXT,
            explanation TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS wardrobe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT,
            category TEXT,
            color TEXT,
            material TEXT,
            brand TEXT,
            brand_confidence TEXT,
            description TEXT,
            image BLOB NOT NULL,
            image_name TEXT,
            image_type TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    # Миграция старой базы.
    for table in ("outfit_analyses", "saved_outfits"):
        if not column_exists(con, table, "user_id"):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")

    if not column_exists(con, "saved_outfits", "images_json"):
        cur.execute(
            "ALTER TABLE saved_outfits ADD COLUMN images_json TEXT"
        )

    con.commit()
    con.close()


init_database()


# =========================================================
# ПАРОЛИ
# =========================================================

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        180_000,
    )
    return (
        "pbkdf2_sha256$180000$"
        + base64.b64encode(salt).decode()
        + "$"
        + base64.b64encode(digest).decode()
    )


def verify_password(password, stored):
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


# =========================================================
# ПОЛЬЗОВАТЕЛИ
# =========================================================

def create_user(email, password):
    email = email.strip().lower()

    if len(email) < 3 or "@" not in email:
        return False, "Введите корректный email."

    if len(password) < 8:
        return False, "Пароль должен содержать минимум 8 символов."

    con = get_db()

    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO users
            (email, password_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (
                email,
                hash_password(password),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

        user_id = cur.lastrowid

        # Старые записи без user_id принадлежат первому созданному
        # пользователю. Новые записи уже всегда привязаны к user_id.
        cur.execute(
            "UPDATE outfit_analyses SET user_id=? WHERE user_id IS NULL",
            (user_id,),
        )
        cur.execute(
            "UPDATE saved_outfits SET user_id=? WHERE user_id IS NULL",
            (user_id,),
        )

        con.commit()
        return True, user_id

    except sqlite3.IntegrityError:
        return False, "Этот email уже зарегистрирован."
    finally:
        con.close()


def authenticate(email, password):
    con = get_db()
    row = con.execute(
        "SELECT * FROM users WHERE email=?",
        (email.strip().lower(),),
    ).fetchone()
    con.close()

    if not row or not verify_password(password, row["password_hash"]):
        return None

    return row


def get_user(user_id):
    con = get_db()
    row = con.execute(
        "SELECT * FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    con.close()
    return row


# =========================================================
# БАЛАНС И ОПЕРАЦИИ
# =========================================================

def get_balance(user_id):
    user = get_user(user_id)
    return int(user["balance"]) if user else 0


def add_transaction(user_id, amount, description):
    con = get_db()
    con.execute(
        """
        INSERT INTO transactions
        (user_id, amount, description, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            amount,
            description,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    con.commit()
    con.close()


def charge_user(user_id, amount, description):
    """
    Безопасное списание внутри одной SQLite-транзакции.
    """
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")

        user = cur.execute(
            "SELECT balance FROM users WHERE id=?",
            (user_id,),
        ).fetchone()

        if not user or int(user["balance"]) < amount:
            con.rollback()
            return False

        cur.execute(
            "UPDATE users SET balance=balance-? WHERE id=?",
            (amount, user_id),
        )

        cur.execute(
            """
            INSERT INTO transactions
            (user_id, amount, description, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                -amount,
                description,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

        con.commit()
        return True
    except Exception:
        con.rollback()
        return False
    finally:
        con.close()


def use_free_analysis(user_id):
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        UPDATE users
        SET free_analysis_used=1
        WHERE id=? AND free_analysis_used=0
        """,
        (user_id,),
    )
    changed = cur.rowcount == 1
    con.commit()
    con.close()
    return changed


def use_free_wardrobe_outfit(user_id):
    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        UPDATE users
        SET free_wardrobe_outfit_used=1
        WHERE id=? AND free_wardrobe_outfit_used=0
        """,
        (user_id,),
    )
    changed = cur.rowcount == 1
    con.commit()
    con.close()
    return changed


def get_transactions(user_id):
    con = get_db()
    rows = con.execute(
        """
        SELECT *
        FROM transactions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user_id,),
    ).fetchall()
    con.close()
    return rows


# =========================================================
# АНАЛИЗЫ
# =========================================================

def save_analysis_to_db(
    user_id,
    style,
    analysis,
    image_bytes=None,
    image_name=None,
    image_type=None,
):
    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        INSERT INTO outfit_analyses
        (user_id, style, analysis, improved_result,
         image, image_name, image_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            style,
            analysis,
            None,
            image_bytes,
            image_name,
            image_type,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    con.commit()
    analysis_id = cur.lastrowid
    con.close()
    return analysis_id


def update_analysis_improvement(analysis_id, user_id, improved_result):
    con = get_db()
    con.execute(
        """
        UPDATE outfit_analyses
        SET improved_result=?
        WHERE id=? AND user_id=?
        """,
        (improved_result, analysis_id, user_id),
    )
    con.commit()
    con.close()


def get_all_analyses(user_id):
    con = get_db()
    rows = con.execute(
        """
        SELECT *
        FROM outfit_analyses
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    con.close()
    return rows


def delete_analysis(analysis_id, user_id):
    con = get_db()
    con.execute(
        "DELETE FROM outfit_analyses WHERE id=? AND user_id=?",
        (analysis_id, user_id),
    )
    con.commit()
    con.close()


def analysis_exists(user_id, style, analysis):
    con = get_db()
    row = con.execute(
        """
        SELECT id
        FROM outfit_analyses
        WHERE user_id=? AND style=? AND analysis=?
        LIMIT 1
        """,
        (user_id, style, analysis),
    ).fetchone()
    con.close()
    return row is not None


# =========================================================
# ГАРДЕРОБ
# =========================================================

def prepare_wardrobe_images(wardrobe_files):
    images = []

    if not wardrobe_files:
        return images

    for number, file in enumerate(wardrobe_files, start=1):
        try:
            images.append(
                {
                    "number": number,
                    "name": file.name or f"item_{number}.jpg",
                    "type": file.type or "image/jpeg",
                    "data": base64.b64encode(
                        file.getvalue()
                    ).decode("utf-8"),
                }
            )
        except Exception:
            pass

    return images


def save_wardrobe_item(user_id, item, file):
    con = get_db()
    con.execute(
        """
        INSERT INTO wardrobe_items
        (user_id, name, category, color, material, brand,
         brand_confidence, description, image, image_name,
         image_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            item.get("name", "Вещь"),
            item.get("category", ""),
            item.get("color", ""),
            item.get("material", ""),
            item.get("brand", ""),
            item.get("brand_confidence", ""),
            item.get("description", ""),
            file.getvalue(),
            file.name,
            file.type or "image/jpeg",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    con.commit()
    con.close()


def get_wardrobe_items(user_id):
    con = get_db()
    rows = con.execute(
        """
        SELECT *
        FROM wardrobe_items
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    con.close()
    return rows


def delete_wardrobe_item(item_id, user_id):
    con = get_db()
    con.execute(
        "DELETE FROM wardrobe_items WHERE id=? AND user_id=?",
        (item_id, user_id),
    )
    con.commit()
    con.close()


def save_wardrobe_outfit(
    user_id,
    name,
    style,
    items,
    explanation,
    wardrobe_files=None,
):
    images = prepare_wardrobe_images(wardrobe_files)
    con = get_db()

    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO saved_outfits
        (user_id, name, style, items_json, explanation,
         created_at, images_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            name,
            style,
            json.dumps(items, ensure_ascii=False),
            explanation,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(images, ensure_ascii=False),
        ),
    )

    con.commit()
    outfit_id = cur.lastrowid
    con.close()
    return outfit_id


def get_saved_wardrobe_outfits(user_id):
    con = get_db()
    rows = con.execute(
        """
        SELECT *
        FROM saved_outfits
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    con.close()
    return rows


def delete_saved_wardrobe_outfit(outfit_id, user_id):
    con = get_db()
    con.execute(
        "DELETE FROM saved_outfits WHERE id=? AND user_id=?",
        (outfit_id, user_id),
    )
    con.commit()
    con.close()


def get_saved_outfit_images(saved):
    try:
        raw = saved["images_json"]
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def image_from_saved_data(image_data):
    try:
        return base64.b64decode(image_data["data"])
    except Exception:
        return None


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ
# =========================================================

def image_to_data_url(uploaded_file):
    encoded = base64.b64encode(
        uploaded_file.getvalue()
    ).decode("utf-8")
    mime = uploaded_file.type or "image/jpeg"
    return f"data:{mime};base64,{encoded}"


def extract_json(text):
    if not text:
        raise ValueError("AI вернул пустой ответ.")

    text = text.strip()
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ai_error_message(error):
    text = str(error).lower()

    if "timeout" in text or "connecttimeout" in text:
        return (
            "⏱️ AI не успел ответить. "
            "Попробуй ещё раз или загрузи фотографию меньшего размера."
        )

    return "❌ Не удалось получить ответ от AI."


# =========================================================
# SESSION
# =========================================================

defaults = {
    "logged_in": False,
    "user_id": None,
    "selected_style": "Indie Sleaze",
    "analysis_result": None,
    "analysis_style": None,
    "improved_result": None,
    "current_analysis_id": None,
    "current_analysis_image": None,
    "wardrobe_data": None,
    "wardrobe_files": [],
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


def logout():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


# =========================================================
# ЭКРАН ВХОДА / РЕГИСТРАЦИИ
# =========================================================

if not st.session_state.logged_in:
    st.markdown(
        """
        <div class="login-wrap">
            <div class="login-title">👕 AI Stylist</div>
            <div class="login-subtitle">
                Твой персональный AI-стилист
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    login_tab, register_tab = st.tabs(
        ["🔑 Войти", "✨ Создать аккаунт"]
    )

    with login_tab:
        with st.form("login_form"):
            email = st.text_input(
                "Email",
                placeholder="you@example.com",
            )
            password = st.text_input(
                "Пароль",
                type="password",
            )
            submitted = st.form_submit_button(
                "🔑 Войти",
                use_container_width=True,
            )

        if submitted:
            user = authenticate(email, password)

            if user:
                st.session_state.logged_in = True
                st.session_state.user_id = user["id"]
                st.rerun()
            else:
                st.error("Неверный email или пароль.")

    with register_tab:
        with st.form("register_form"):
            new_email = st.text_input(
                "Email",
                placeholder="you@example.com",
            )
            new_password = st.text_input(
                "Пароль",
                type="password",
                help="Минимум 8 символов.",
            )
            new_password2 = st.text_input(
                "Повтори пароль",
                type="password",
            )

            register = st.form_submit_button(
                "✨ Зарегистрироваться",
                use_container_width=True,
            )

        if register:
            if new_password != new_password2:
                st.error("Пароли не совпадают.")
            else:
                ok, result = create_user(
                    new_email,
                    new_password,
                )

                if ok:
                    st.session_state.logged_in = True
                    st.session_state.user_id = result
                    st.success("Аккаунт создан!")
                    st.rerun()
                else:
                    st.error(result)

    st.stop()


# =========================================================
# ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ
# =========================================================

user = get_user(st.session_state.user_id)

if not user:
    st.session_state.logged_in = False
    st.session_state.user_id = None
    st.rerun()

user_id = user["id"]
balance = int(user["balance"])


# =========================================================
# ШАПКА
# =========================================================

st.markdown(
    """
    <div class="hero">
        <div class="hero-title">👕 AI Stylist</div>
        <div class="hero-subtitle">
            Собирай образы, анализируй стиль и управляй своим гардеробом
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

top_left, top_mid, top_right = st.columns([2, 2, 1])

with top_left:
    st.caption(f"👋 {user['email']}")

with top_mid:
    st.caption(f"💰 Баланс: **{balance} ₽**")

with top_right:
    if st.button("Выйти", use_container_width=True):
        logout()


# =========================================================
# ВКЛАДКИ
# =========================================================

tab_analysis, tab_wardrobe, tab_account = st.tabs(
    [
        "✨ Оценка образа",
        "👗 Мой гардероб",
        "👤 Мой аккаунт",
    ]
)


# =========================================================
# ВКЛАДКА — ОЦЕНКА ОБРАЗА
# =========================================================

with tab_analysis:

    st.markdown(
        '<div class="section-title">✨ Оценка образа</div>',
        unsafe_allow_html=True,
    )

    st.caption(
        "Загрузи фото образа — AI разберёт одежду, цвета, "
        "пропорции и соответствие выбранному стилю."
    )

    st.subheader("🎨 Выбери стиль")

    style_cols = st.columns(5)

    for i, style_name in enumerate(STYLES):
        with style_cols[i]:
            if st.button(
                style_name,
                use_container_width=True,
                key=f"style_{i}",
            ):
                st.session_state.selected_style = style_name

    style = st.session_state.selected_style

    st.info(f"🎨 Сейчас выбран стиль: **{style}**")

    uploaded_file = st.file_uploader(
        "📸 Фотография образа",
        type=["jpg", "jpeg", "png"],
        key="outfit_uploader",
    )

    if uploaded_file:
        st.image(
            uploaded_file,
            caption="Твой образ",
            width="stretch",
        )

        free_available = int(user["free_analysis_used"]) == 0

        price_text = (
            "🎁 Первая оценка — бесплатно"
            if free_available
            else f"💳 Стоимость оценки — {PRICES['analysis']} ₽"
        )

        st.markdown(
            f"""
            <div class="card">
                <b>✨ Анализ образа</b>
                <div class="price">
                    {"Бесплатно" if free_available else f"{PRICES['analysis']} ₽"}
                </div>
                <div class="muted">{price_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button(
            "✨ Оценить мой образ",
            use_container_width=True,
            type="primary",
            key="analyze_outfit",
        ):
            if not free_available:
                if balance < PRICES["analysis"]:
                    st.error(
                        f"Недостаточно средств. "
                        f"Нужно {PRICES['analysis']} ₽."
                    )
                    st.info(
                        "Пополнение баланса можно подключить "
                        "через платёжную систему после её настройки."
                    )
                    st.stop()

            with st.spinner("AI анализирует твой образ..."):
                try:
                    image_url = image_to_data_url(uploaded_file)

                    prompt = f"""
Ты — профессиональный AI-стилист.

Пользователь выбрал стиль: {style}

Проанализируй одежду человека на фотографии
с точки зрения соответствия стилю "{style}".

Ответь на русском языке.

Используй формат:

ОЦЕНКА: X/10

ЧТО ХОРОШО:
- пункт
- пункт
- пункт

ЧТО МОЖНО УЛУЧШИТЬ:
- пункт
- пункт
- пункт

ЧТО ДОБАВИТЬ:
- пункт
- пункт
- пункт

СОВЕТ:
Короткий конкретный совет.

Анализируй только одежду, цвета, вещи, обувь,
аксессуары, сочетания, силуэт, пропорции и стиль.

Не оценивай лицо, тело, привлекательность
или физические особенности человека.
"""

                    response = client.chat.completions.create(
                        model=MODEL,
                        max_tokens=1600,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": prompt,
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": image_url
                                        },
                                    },
                                ],
                            }
                        ],
                    )

                    result = response.choices[0].message.content

                    if not result:
                        raise ValueError("AI вернул пустой результат.")

                    # Списываем только после успешного ответа AI.
                    if free_available:
                        if not use_free_analysis(user_id):
                            raise RuntimeError(
                                "Не удалось применить бесплатную попытку."
                            )
                    else:
                        if not charge_user(
                            user_id,
                            PRICES["analysis"],
                            "Оценка образа",
                        ):
                            raise RuntimeError(
                                "Не удалось списать средства."
                            )

                    st.session_state.analysis_result = result
                    st.session_state.analysis_style = style
                    st.session_state.improved_result = None
                    st.session_state.current_analysis_id = None
                    st.session_state.current_analysis_image = uploaded_file.getvalue()

                    st.success("Анализ готов! ✅")
                    st.rerun()

                except Exception as e:
                    st.error(ai_error_message(e))
                    st.code(str(e))

    # Результат анализа
    if st.session_state.analysis_result:
        st.divider()

        st.subheader("🤖 Результат")

        st.info(
            f"🎨 Стиль: **{st.session_state.analysis_style or style}**"
        )

        st.markdown(st.session_state.analysis_result)

        if st.session_state.current_analysis_id:
            st.success("⭐ Анализ уже сохранён.")
        else:
            if st.button(
                "💾 Сохранить анализ",
                use_container_width=True,
                key="save_analysis",
            ):
                try:
                    analysis_style = (
                        st.session_state.analysis_style or style
                    )

                    if analysis_exists(
                        user_id,
                        analysis_style,
                        st.session_state.analysis_result,
                    ):
                        st.warning("Этот анализ уже сохранён.")
                    else:
                        saved_id = save_analysis_to_db(
                            user_id=user_id,
                            style=analysis_style,
                            analysis=st.session_state.analysis_result,
                            image_bytes=st.session_state.current_analysis_image,
                            image_name="outfit.jpg",
                            image_type="image/jpeg",
                        )

                        st.session_state.current_analysis_id = saved_id
                        st.success("⭐ Анализ сохранён.")
                        st.rerun()

                except Exception as e:
                    st.error("Не удалось сохранить анализ.")
                    st.code(str(e))

        st.divider()

        st.subheader("✨ Улучшить образ")

        st.markdown(
            f"""
            <div class="card">
                <b>✨ Персональное улучшение</b>
                <div class="price">{PRICES['improve']} ₽</div>
                <div class="muted">
                    AI составит конкретный план изменений.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button(
            f"✨ Улучшить образ — {PRICES['improve']} ₽",
            use_container_width=True,
            key="improve_outfit",
        ):
            if balance < PRICES["improve"]:
                st.error(
                    f"Недостаточно средств. Нужно {PRICES['improve']} ₽."
                )
            else:
                with st.spinner("AI разрабатывает улучшенную версию..."):
                    try:
                        analysis_style = (
                            st.session_state.analysis_style or style
                        )

                        improve_prompt = f"""
Ты — профессиональный AI-стилист.

Стиль пользователя: {analysis_style}

Анализ текущего образа:
{st.session_state.analysis_result}

Составь конкретный план улучшения.

Ответь на русском языке.

🎯 ГЛАВНАЯ ПРОБЛЕМА
Что сильнее всего можно улучшить.

👕 ЧТО ИЗМЕНИТЬ
- конкретная вещь
- конкретная вещь
- конкретная вещь

➕ ЧТО ДОБАВИТЬ
- конкретный предмет
- конкретный аксессуар

🎨 ЦВЕТА
Какие цвета лучше использовать.

📐 ПРОПОРЦИИ
Как улучшить сочетание верха, низа и обуви.

🔥 ГОТОВЫЙ ВАРИАНТ
Опиши итоговый образ.

💡 ГЛАВНЫЙ СОВЕТ
Один самый практичный совет.

Не оценивай лицо, тело или привлекательность.
"""

                        response = client.chat.completions.create(
                            model=MODEL,
                            max_tokens=1600,
                            messages=[
                                {
                                    "role": "user",
                                    "content": improve_prompt,
                                }
                            ],
                        )

                        improved = response.choices[0].message.content

                        if not improved:
                            raise ValueError("AI вернул пустой результат.")

                        if not charge_user(
                            user_id,
                            PRICES["improve"],
                            "Улучшение образа",
                        ):
                            raise RuntimeError(
                                "Не удалось списать средства."
                            )

                        st.session_state.improved_result = improved

                        if st.session_state.current_analysis_id:
                            update_analysis_improvement(
                                st.session_state.current_analysis_id,
                                user_id,
                                improved,
                            )

                        st.success("✨ Готово!")
                        st.rerun()

                    except Exception as e:
                        st.error(ai_error_message(e))
                        st.code(str(e))

        if st.session_state.improved_result:
            st.divider()
            st.subheader("✨ Улучшенная версия")
            st.markdown(st.session_state.improved_result)

    # Сохранённые анализы
    st.divider()
    st.subheader("🗂️ Мои сохранённые анализы")

    saved_analyses = get_all_analyses(user_id)

    if not saved_analyses:
        st.info("Пока нет сохранённых анализов.")
    else:
        for saved in saved_analyses:
            with st.expander(
                f"👕 {saved['style']} • {saved['created_at']}"
            ):
                if saved["image"]:
                    st.image(
                        saved["image"],
                        caption=saved["image_name"] or "Образ",
                        width="stretch",
                    )

                st.markdown(saved["analysis"])

                if saved["improved_result"]:
                    st.divider()
                    st.subheader("✨ Улучшенная версия")
                    st.markdown(saved["improved_result"])

                if st.button(
                    "🗑️ Удалить",
                    key=f"delete_analysis_{saved['id']}",
                ):
                    delete_analysis(saved["id"], user_id)
                    st.rerun()


# =========================================================
# ВКЛАДКА — ГАРДЕРОБ
# =========================================================

with tab_wardrobe:

    st.markdown(
        '<div class="section-title">👗 Мой гардероб</div>',
        unsafe_allow_html=True,
    )

    st.caption(
        "Загружай вещи бесплатно. AI распознает их и поможет "
        "собрать образы."
    )

    st.subheader("➕ Добавить вещи")

    wardrobe_files = st.file_uploader(
        "Фотографии одежды",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="wardrobe_uploader",
    )

    if wardrobe_files:
        if len(wardrobe_files) > 6:
            st.warning("Максимум 6 фотографий за один анализ.")
        else:
            cols = st.columns(min(3, len(wardrobe_files)))

            for i, file in enumerate(wardrobe_files):
                with cols[i % len(cols)]:
                    st.image(
                        file,
                        caption=f"Вещь №{i + 1}",
                        width="stretch",
                    )

            st.info(
                "💚 Загрузка одежды бесплатна."
            )

            build_free = int(user["free_wardrobe_outfit_used"]) == 0

            st.markdown(
                f"""
                <div class="card">
                    <b>✨ Собрать 3 образа</b>
                    <div class="price">
                        {"Бесплатно" if build_free else f"{PRICES['wardrobe_outfit']} ₽"}
                    </div>
                    <div class="muted">
                        Используются только загруженные вещи.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if st.button(
                "✨ Собрать образы",
                use_container_width=True,
                type="primary",
                key="build_wardrobe",
            ):
                if not build_free and balance < PRICES["wardrobe_outfit"]:
                    st.error(
                        f"Недостаточно средств. Нужно "
                        f"{PRICES['wardrobe_outfit']} ₽."
                    )
                else:
                    with st.spinner("AI анализирует гардероб..."):
                        try:
                            style = st.session_state.selected_style

                            wardrobe_prompt = f"""
Ты — профессиональный AI-стилист.

Стиль пользователя: {style}

Пользователь загрузил {len(wardrobe_files)} фотографий.

Фото 1 = вещь 1.
Фото 2 = вещь 2.
И так далее.

Проанализируй каждую вещь.

Для каждой определи:
- название;
- категорию;
- цвет;
- материал или фактуру;
- бренд;
- уверенность бренда;
- описание.

НИКОГДА НЕ ПРИДУМЫВАЙ БРЕНД.
Если бренд не виден, напиши "Не удалось определить".

После анализа создай 3 разных образа.

Используй ТОЛЬКО загруженные вещи.
Не добавляй вещи, которых нет на фотографиях.

Верни ТОЛЬКО JSON.

Структура:

{{
    "items": [
        {{
            "number": 1,
            "name": "название",
            "category": "категория",
            "color": "цвет",
            "material": "материал",
            "brand": "бренд",
            "brand_confidence": "высокая",
            "description": "описание"
        }}
    ],
    "outfits": [
        {{
            "number": 1,
            "name": "название образа",
            "items": [1, 2],
            "explanation": "описание"
        }},
        {{
            "number": 2,
            "name": "название образа",
            "items": [2, 3],
            "explanation": "описание"
        }},
        {{
            "number": 3,
            "name": "название образа",
            "items": [1, 3],
            "explanation": "описание"
        }}
    ],
    "main_advice": "совет"
}}

Номера вещей должны соответствовать фотографиям.

Не анализируй лицо, тело или привлекательность.
"""

                            content = [
                                {
                                    "type": "text",
                                    "text": wardrobe_prompt,
                                }
                            ]

                            for file in wardrobe_files:
                                content.append(
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": image_to_data_url(file)
                                        },
                                    }
                                )

                            response = client.chat.completions.create(
                                model=MODEL,
                                max_tokens=3000,
                                messages=[
                                    {
                                        "role": "user",
                                        "content": content,
                                    }
                                ],
                            )

                            wardrobe_text = (
                                response.choices[0].message.content
                            )

                            wardrobe_data = extract_json(wardrobe_text)

                            if not isinstance(wardrobe_data, dict):
                                raise ValueError(
                                    "Неверный формат JSON."
                                )

                            if build_free:
                                if not use_free_wardrobe_outfit(user_id):
                                    raise RuntimeError(
                                        "Не удалось применить бесплатную попытку."
                                    )
                            else:
                                if not charge_user(
                                    user_id,
                                    PRICES["wardrobe_outfit"],
                                    "Сбор образов из гардероба",
                                ):
                                    raise RuntimeError(
                                        "Не удалось списать средства."
                                    )

                            st.session_state.wardrobe_data = wardrobe_data
                            st.session_state.wardrobe_files = wardrobe_files

                            # Сохраняем вещи в постоянный гардероб.
                            for index, item in enumerate(
                                wardrobe_data.get("items", [])
                            ):
                                number = safe_int(item.get("number"))

                                if (
                                    number
                                    and 1 <= number <= len(wardrobe_files)
                                ):
                                    save_wardrobe_item(
                                        user_id,
                                        item,
                                        wardrobe_files[number - 1],
                                    )

                            st.success("🎉 Гардероб готов!")
                            st.rerun()

                        except Exception as e:
                            st.error(ai_error_message(e))
                            st.code(str(e))

    # AI-результат
    if st.session_state.wardrobe_data:
        wardrobe_data = st.session_state.wardrobe_data

        st.divider()
        st.subheader("✨ Результат AI")

        items = wardrobe_data.get("items", [])
        item_by_number = {}

        for item in items:
            number = safe_int(item.get("number"))
            if number:
                item_by_number[number] = item

        if items:
            st.write("### 👕 Распознанные вещи")

            item_cols = st.columns(2)

            for index, item in enumerate(items):
                with item_cols[index % 2]:
                    st.markdown(
                        f"**{item.get('name', 'Вещь')}**"
                    )
                    st.caption(
                        f"№{item.get('number', '?')} · "
                        f"{item.get('category', '—')}"
                    )
                    st.write(
                        f"Цвет: {item.get('color', '—')}"
                    )
                    st.write(
                        f"Материал: {item.get('material', '—')}"
                    )
                    st.write(
                        f"Бренд: {item.get('brand', '—')}"
                    )

        outfits = wardrobe_data.get("outfits", [])

        if outfits:
            st.write("### 🔥 Собранные образы")

            for outfit_index, outfit in enumerate(outfits):
                with st.container(border=True):
                    st.markdown(
                        f"#### 🔥 {outfit.get('name', 'Образ')}"
                    )

                    selected_items = []

                    for number in outfit.get("items", []):
                        number = safe_int(number)
                        if number in item_by_number:
                            selected_items.append(
                                item_by_number[number]
                            )

                    if selected_items:
                        image_cols = st.columns(
                            min(3, len(selected_items))
                        )

                        for index, item in enumerate(selected_items):
                            with image_cols[index % len(image_cols)]:
                                number = safe_int(
                                    item.get("number")
                                )

                                files = st.session_state.wardrobe_files

                                if (
                                    files
                                    and number
                                    and number <= len(files)
                                ):
                                    st.image(
                                        files[number - 1],
                                        width="stretch",
                                    )

                                st.caption(
                                    item.get("name", "Вещь")
                                )

                    explanation = outfit.get(
                        "explanation",
                        "",
                    )

                    if explanation:
                        st.info(f"💡 {explanation}")

                    if st.button(
                        "💾 Сохранить образ",
                        use_container_width=True,
                        key=f"save_wardrobe_{outfit_index}",
                    ):
                        try:
                            saved_id = save_wardrobe_outfit(
                                user_id=user_id,
                                name=outfit.get("name", "Образ"),
                                style=st.session_state.selected_style,
                                items=outfit.get("items", []),
                                explanation=explanation,
                                wardrobe_files=st.session_state.wardrobe_files,
                            )
                            st.success("⭐ Образ сохранён.")
                        except Exception as e:
                            st.error("Не удалось сохранить образ.")
                            st.code(str(e))

        if wardrobe_data.get("main_advice"):
            st.info(
                f"💡 Совет стилиста: "
                f"{wardrobe_data['main_advice']}"
            )

    # Постоянный гардероб
    st.divider()
    st.subheader("🧥 Мои вещи")

    persistent_items = get_wardrobe_items(user_id)

    if not persistent_items:
        st.info(
            "Пока нет сохранённых вещей. "
            "Загрузи одежду выше."
        )
    else:
        item_cols = st.columns(3)

        for index, item in enumerate(persistent_items):
            with item_cols[index % 3]:
                st.image(
                    item["image"],
                    width="stretch",
                )
                st.markdown(
                    f"**{item['name'] or 'Вещь'}**"
                )
                st.caption(
                    f"{item['category'] or 'Категория не определена'}"
                )

                if item["color"]:
                    st.write(f"🎨 {item['color']}")

                if st.button(
                    "🗑️ Удалить",
                    key=f"delete_item_{item['id']}",
                ):
                    delete_wardrobe_item(
                        item["id"],
                        user_id,
                    )
                    st.rerun()

    # Сохранённые образы
    st.divider()
    st.subheader("⭐ Мои сохранённые образы")

    saved_wardrobe = get_saved_wardrobe_outfits(user_id)

    if not saved_wardrobe:
        st.info("Сохранённых образов пока нет.")
    else:
        for saved in saved_wardrobe:
            with st.expander(
                f"⭐ {saved['name']} • {saved['created_at']}"
            ):
                st.caption(
                    f"Стиль: {saved['style'] or '—'}"
                )

                try:
                    saved_items = json.loads(
                        saved["items_json"]
                    )
                except Exception:
                    saved_items = []

                if saved_items:
                    st.write(
                        "Вещи: "
                        + ", ".join(
                            f"№{x}" for x in saved_items
                        )
                    )

                saved_images = get_saved_outfit_images(saved)

                if saved_images:
                    image_cols = st.columns(
                        min(3, len(saved_images))
                    )

                    for image_index, image_data in enumerate(
                        saved_images
                    ):
                        image_bytes = image_from_saved_data(
                            image_data
                        )

                        if image_bytes:
                            with image_cols[
                                image_index % len(image_cols)
                            ]:
                                st.image(
                                    image_bytes,
                                    caption=image_data.get(
                                        "name",
                                        "Вещь",
                                    ),
                                    width="stretch",
                                )

                if saved["explanation"]:
                    st.info(saved["explanation"])

                if st.button(
                    "🗑️ Удалить образ",
                    key=f"delete_saved_outfit_{saved['id']}",
                ):
                    delete_saved_wardrobe_outfit(
                        saved["id"],
                        user_id,
                    )
                    st.rerun()


# =========================================================
# ВКЛАДКА — АККАУНТ
# =========================================================

with tab_account:

    st.markdown(
        '<div class="section-title">👤 Мой аккаунт</div>',
        unsafe_allow_html=True,
    )

    st.write(f"Добро пожаловать, **{user['email']}** 👋")

    current_balance = get_balance(user_id)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            f"""
            <div class="card">
                <div class="muted">💰 Баланс</div>
                <div class="balance">{current_balance} ₽</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        free_analysis = int(user["free_analysis_used"]) == 0
        free_wardrobe = int(
            user["free_wardrobe_outfit_used"]
        ) == 0

        st.markdown(
            f"""
            <div class="card">
                <b>🎁 Бесплатные попытки</b><br><br>
                {"🟢" if free_analysis else "⚪"}
                Оценка образа<br>
                {"🟢" if free_wardrobe else "⚪"}
                Сбор образа из гардероба
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.subheader("💳 Цены")

    price_cols = st.columns(3)

    with price_cols[0]:
        st.markdown(
            f"""
            <div class="card">
                <b>✨ Оценка</b>
                <div class="price">20 ₽</div>
                <div class="muted">после первой бесплатной</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with price_cols[1]:
        st.markdown(
            f"""
            <div class="card">
                <b>✨ Улучшение</b>
                <div class="price">10 ₽</div>
                <div class="muted">за один результат</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with price_cols[2]:
        st.markdown(
            f"""
            <div class="card">
                <b>👗 Образ из гардероба</b>
                <div class="price">50 ₽</div>
                <div class="muted">после первой бесплатной</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.subheader("➕ Пополнение баланса")

    st.info(
        "Реальное пополнение пока не подключено. "
        "Следующим этапом можно подключить ЮKassa, "
        "чтобы деньги зачислялись только после подтверждения платежа."
    )

    st.divider()

    st.subheader("📋 История операций")

    transactions = get_transactions(user_id)

    if not transactions:
        st.caption("Операций пока нет.")
    else:
        for transaction in transactions:
            amount = int(transaction["amount"])
            sign = "+" if amount > 0 else ""

            st.write(
                f"**{sign}{amount} ₽** · "
                f"{transaction['description']} · "
                f"{transaction['created_at']}"
            )

    st.divider()

    if st.button(
        "🚪 Выйти из аккаунта",
        use_container_width=True,
    ):
        logout()


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "👕 AI Stylist • AI-анализ одежды • "
    "Персональный гардероб • Постоянное хранение данных"
)
