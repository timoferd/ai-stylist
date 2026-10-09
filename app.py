
import os
import io
import json
import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime
from html import escape

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps

# =====================================================
# CONFIG
# =====================================================

load_dotenv()

st.set_page_config(
    page_title="AI Stylist",
    page_icon="👕",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "ai_stylist.db")

MODEL = os.getenv("AI_MODEL", "gpt-5.6-sol")

PRICES = {
    "analysis": 20,
    "improve": 10,
    "outfit": 50,
}

STYLES = [
    "Indie Sleaze",
    "Drip / Streetwear",
    "Old Money",
    "Y2K",
    "Minimalism",
]

CATEGORIES = [
    "Верхняя одежда",
    "Футболка / топ",
    "Рубашка",
    "Свитер / худи",
    "Брюки",
    "Джинсы",
    "Юбка",
    "Платье",
    "Обувь",
    "Сумка",
    "Аксессуар",
    "Другое",
]

api_key = os.getenv("AITUNNEL_API_KEY")
client = (
    OpenAI(
        api_key=api_key,
        base_url="https://api.aitunnel.ru/v1",
        timeout=180,
        max_retries=2,
    )
    if api_key else None
)

# =====================================================
# STYLE
# =====================================================

st.markdown(
    """
    <style>
    #MainMenu, footer, header {
        visibility: hidden;
    }

    .block-container {
        max-width: 1180px;
        padding-top: 1.5rem;
        padding-bottom: 3rem;
    }

    .hero {
        padding: 28px;
        border-radius: 24px;
        background: linear-gradient(135deg, #f4f4f4, #ffffff);
        border: 1px solid #e8e8e8;
        margin-bottom: 20px;
        color: #111111 !important;
    }

    .hero-title {
        color: #111111 !important;
        font-size: 38px;
        font-weight: 850;
        letter-spacing: -1px;
        line-height: 1.25;
    }

    .hero-subtitle {
        color: #333333 !important;
        margin-top: 8px;
    }

    .hero * {
        color: #111111 !important;
    }

    [data-testid="stMetricValue"] {
        color: #111111;
    }

    .muted {
        color: #777777;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

# =====================================================
# DATABASE AND MIGRATIONS
# =====================================================

def get_db():
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def add_missing_columns(con, table, columns):
    """Safely add missing columns to an existing SQLite table."""
    existing = {
        row["name"]
        for row in con.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
    }

    for name, definition in columns.items():
        if name not in existing:
            con.execute(
                f'ALTER TABLE "{table}" '
                f'ADD COLUMN "{name}" {definition}'
            )


def init_database():
    with get_db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0,
            free_analysis_used INTEGER NOT NULL DEFAULT 0,
            free_outfit_used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wardrobe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            color TEXT DEFAULT '',
            material TEXT DEFAULT '',
            brand TEXT DEFAULT '',
            description TEXT DEFAULT '',
            image BLOB NOT NULL,
            image_name TEXT DEFAULT '',
            image_type TEXT DEFAULT 'image/jpeg',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS outfit_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            style TEXT NOT NULL DEFAULT '',
            analysis TEXT NOT NULL DEFAULT '',
            improved_result TEXT DEFAULT '',
            image BLOB,
            created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS saved_outfits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL DEFAULT 'Образ',
            style TEXT DEFAULT '',
            items_json TEXT DEFAULT '[]',
            explanation TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
                ON DELETE CASCADE
        );
        """)

        # Critical migration: existing users may lack this column.
        add_missing_columns(con, "users", {
            "balance": "INTEGER NOT NULL DEFAULT 0",
            "free_analysis_used": "INTEGER NOT NULL DEFAULT 0",
            "free_outfit_used": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        })

        add_missing_columns(con, "outfit_analyses", {
            "user_id": "INTEGER",
            "style": "TEXT NOT NULL DEFAULT ''",
            "analysis": "TEXT NOT NULL DEFAULT ''",
            "improved_result": "TEXT DEFAULT ''",
            "image": "BLOB",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        })

        add_missing_columns(con, "saved_outfits", {
            "user_id": "INTEGER",
            "name": "TEXT NOT NULL DEFAULT 'Образ'",
            "style": "TEXT DEFAULT ''",
            "items_json": "TEXT DEFAULT '[]'",
            "explanation": "TEXT DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        })

        add_missing_columns(con, "wardrobe_items", {
            "category": "TEXT DEFAULT ''",
            "color": "TEXT DEFAULT ''",
            "material": "TEXT DEFAULT ''",
            "brand": "TEXT DEFAULT ''",
            "description": "TEXT DEFAULT ''",
            "image_name": "TEXT DEFAULT ''",
            "image_type": "TEXT DEFAULT 'image/jpeg'",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        })


init_database()

# =====================================================
# GENERAL HELPERS
# =====================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password):
    salt = secrets.token_bytes(16)
    iterations = 180_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, iterations
    )
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(digest).decode()}"
    )


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.b64decode(salt),
            int(iterations),
        )

        return secrets.compare_digest(
            actual, base64.b64decode(digest)
        )
    except (ValueError, TypeError, AttributeError):
        return False


def get_user(user_id):
    with get_db() as con:
        return con.execute(
            "SELECT * FROM users WHERE id=?",
            (user_id,),
        ).fetchone()


def register_user(email, password):
    email = email.strip().lower()

    if "@" not in email or len(email) < 3:
        return False, "Введите корректный email."

    if len(password) < 8:
        return False, "Пароль должен содержать минимум 8 символов."

    try:
        with get_db() as con:
            cur = con.execute(
                """
                INSERT INTO users
                    (email, password_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (email, hash_password(password), now()),
            )
            return True, cur.lastrowid
    except sqlite3.IntegrityError:
        return False, "Этот email уже зарегистрирован."


def authenticate(email, password):
    with get_db() as con:
        user = con.execute(
            "SELECT * FROM users WHERE email=?",
            (email.strip().lower(),),
        ).fetchone()

    if user and verify_password(password, user["password_hash"]):
        return user

    return None


def image_bytes(uploaded_file):
    img = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1600, 1600))

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def image_data_url(data):
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def call_ai(messages, max_tokens=2000):
    if client is None:
        raise RuntimeError(
            "Не найден AITUNNEL_API_KEY. "
            "Добавь ключ в переменные окружения."
        )

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=max_tokens,
    )

    result = response.choices[0].message.content

    if not result:
        raise RuntimeError("AI вернул пустой ответ.")

    return result


def parse_json(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

        raise ValueError("AI вернул некорректный JSON.")


# =====================================================
# BALANCE AND CHARGING
# =====================================================

def charge_user(user_id, amount, description):
    """Atomically charge balance and record a transaction."""
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")

        user = con.execute(
            "SELECT balance FROM users WHERE id=?",
            (user_id,),
        ).fetchone()

        if not user or user["balance"] < amount:
            return False

        con.execute(
            "UPDATE users SET balance=balance-? WHERE id=?",
            (amount, user_id),
        )

        con.execute(
            """
            INSERT INTO transactions
                (user_id, amount, description, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, -amount, description, now()),
        )

        return True


def use_free_attempt(user_id, column):
    allowed = ("free_analysis_used", "free_outfit_used")

    if column not in allowed:
        raise ValueError("Недопустимое поле бесплатной попытки.")

    with get_db() as con:
        cur = con.execute(
            f"""
            UPDATE users SET {column}=1
            WHERE id=? AND {column}=0
            """,
            (user_id,),
        )
        return cur.rowcount == 1


# =====================================================
# WARDROBE OPERATIONS
# =====================================================

def get_wardrobe_items(user_id):
    with get_db() as con:
        return con.execute(
            """
            SELECT * FROM wardrobe_items
            WHERE user_id=?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()


def add_wardrobe_item(
    user_id, name, category, color, material,
    brand, description, photo, photo_name="photo.jpg"
):
    with get_db() as con:
        cur = con.execute(
            """
            INSERT INTO wardrobe_items (
                user_id, name, category, color, material,
                brand, description, image, image_name,
                image_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                name.strip() or "Вещь",
                category,
                color,
                material,
                brand,
                description,
                sqlite3.Binary(photo),
                photo_name,
                "image/jpeg",
                now(),
            ),
        )
        return cur.lastrowid


def delete_wardrobe_item(item_id, user_id):
    with get_db() as con:
        cur = con.execute(
            "DELETE FROM wardrobe_items WHERE id=? AND user_id=?",
            (item_id, user_id),
        )
        return cur.rowcount == 1


def update_wardrobe_item(
    item_id, user_id, name, category,
    color, material, brand, description
):
    with get_db() as con:
        con.execute(
            """
            UPDATE wardrobe_items
            SET name=?, category=?, color=?, material=?,
                brand=?, description=?
            WHERE id=? AND user_id=?
            """,
            (
                name.strip() or "Вещь",
                category,
                color,
                material,
                brand,
                description,
                item_id,
                user_id,
            ),
        )


# =====================================================
# SAVED OUTFITS AND ANALYSES
# =====================================================

def save_outfit(user_id, name, style, items, explanation):
    with get_db() as con:
        cur = con.execute(
            """
            INSERT INTO saved_outfits
                (user_id, name, style, items_json,
                 explanation, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                name,
                style,
                json.dumps(items, ensure_ascii=False),
                explanation,
                now(),
            ),
        )
        return cur.lastrowid


def get_saved_outfits(user_id):
    with get_db() as con:
        return con.execute(
            """
            SELECT * FROM saved_outfits
            WHERE user_id=?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()


def delete_saved_outfit(outfit_id, user_id):
    with get_db() as con:
        con.execute(
            "DELETE FROM saved_outfits WHERE id=? AND user_id=?",
            (outfit_id, user_id),
        )


def save_analysis(user_id, style, result, photo):
    with get_db() as con:
        cur = con.execute(
            """
            INSERT INTO outfit_analyses
                (user_id, style, analysis, image, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                style,
                result,
                sqlite3.Binary(photo) if photo else None,
                now(),
            ),
        )
        return cur.lastrowid


def get_analyses(user_id):
    with get_db() as con:
        return con.execute(
            """
            SELECT * FROM outfit_analyses
            WHERE user_id=?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()


def delete_analysis(analysis_id, user_id):
    with get_db() as con:
        con.execute(
            "DELETE FROM outfit_analyses WHERE id=? AND user_id=?",
            (analysis_id, user_id),
        )


# =====================================================
# SESSION AND LOGOUT
# =====================================================

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "selected_style" not in st.session_state:
    st.session_state.selected_style = STYLES[0]


def logout():
    st.session_state.user_id = None
    st.session_state.pop("last_analysis", None)


# =====================================================
# LOGIN / REGISTRATION
# =====================================================

if not st.session_state.user_id:
    st.markdown(
        """
        <div class="hero">
            <div class="hero-title">👕 AI Stylist</div>
            <div class="hero-subtitle">
                Твой персональный AI-стилист
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    login_tab, register_tab = st.tabs(
        ["🔑 Войти", "✨ Регистрация"]
    )

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button(
                "Войти", use_container_width=True
            )

        if submitted:
            user = authenticate(email, password)

            if user:
                st.session_state.user_id = user["id"]
                st.rerun()
            else:
                st.error("Неверный email или пароль.")

    with register_tab:
        with st.form("register_form"):
            new_email = st.text_input(
                "Email", key="register_email"
            )
            new_password = st.text_input(
                "Пароль — минимум 8 символов",
                type="password",
                key="register_password",
            )
            repeat_password = st.text_input(
                "Повтори пароль",
                type="password",
            )
            submitted = st.form_submit_button(
                "Создать аккаунт",
                use_container_width=True,
            )

        if submitted:
            if new_password != repeat_password:
                st.error("Пароли не совпадают.")
            else:
                ok, result = register_user(
                    new_email, new_password
                )

                if ok:
                    st.session_state.user_id = result
                    st.success("Аккаунт создан.")
                    st.rerun()
                else:
                    st.error(result)

    st.stop()


user = get_user(st.session_state.user_id)

if not user:
    logout()
    st.rerun()

user_id = user["id"]

# =====================================================
# HEADER
# =====================================================

st.markdown(
    """
    <div class="hero">
        <div class="hero-title">👕 AI Stylist</div>
        <div class="hero-subtitle">
            Собирай образы, анализируй стиль и управляй гардеробом
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

left, middle, right = st.columns([3, 2, 1])

with left:
    st.caption(f"👋 {escape(user['email'])}")

with middle:
    st.metric("Баланс", f"{user['balance']} ₽")

with right:
    st.button(
        "Выйти",
        on_click=logout,
        use_container_width=True,
    )

tab_analysis, tab_wardrobe, tab_account = st.tabs(
    ["✨ Оценка образа", "👗 Мой гардероб", "👤 Аккаунт"]
)

# =====================================================
# OUTFIT ANALYSIS
# =====================================================

with tab_analysis:
    st.header("✨ Оценка образа")
    st.write("Загрузи фотографию и получи рекомендации по стилю.")

    st.session_state.selected_style = st.selectbox(
        "Выбери стиль",
        STYLES,
        index=STYLES.index(st.session_state.selected_style),
    )

    outfit_photo = st.file_uploader(
        "Фотография образа",
        type=["jpg", "jpeg", "png"],
        key="analysis_photo",
    )

    if outfit_photo:
        try:
            photo = image_bytes(outfit_photo)
            st.image(
                photo,
                caption="Твой образ",
                use_container_width=True,
            )

            user = get_user(user_id)
            free_available = not user["free_analysis_used"]
            price = 0 if free_available else PRICES["analysis"]

            st.write(
                "Первая оценка бесплатна."
                if free_available
                else f"Стоимость анализа: {price} ₽"
            )

            if st.button(
                "✨ Проанализировать образ",
                type="primary",
            ):
                user = get_user(user_id)
                free_available = not user["free_analysis_used"]
                price = 0 if free_available else PRICES["analysis"]

                if not free_available and user["balance"] < price:
                    st.error("Недостаточно средств.")
                else:
                    try:
                        with st.spinner("AI анализирует образ..."):
                            prompt = f"""
Ты — профессиональный стилист.

Проанализируй одежду, обувь, аксессуары, цвета,
сочетания и соответствие стилю
{st.session_state.selected_style}.

Не оценивай лицо, тело или привлекательность.
Отвечай на русском языке в Markdown.

### Оценка: X/10
### Что хорошо
### Что улучшить
### Что добавить
### Совет стилиста
"""

                            result = call_ai([{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": image_data_url(photo)
                                        },
                                    },
                                ],
                            }])

                            # Charge only after AI successfully returns.
                            if free_available:
                                if not use_free_attempt(
                                    user_id, "free_analysis_used"
                                ):
                                    raise RuntimeError(
                                        "Бесплатная попытка уже использована."
                                    )
                            else:
                                if not charge_user(
                                    user_id, price, "Оценка образа"
                                ):
                                    raise RuntimeError(
                                        "Не удалось списать средства."
                                    )

                            save_analysis(
                                user_id,
                                st.session_state.selected_style,
                                result,
                                photo,
                            )

                            st.session_state.last_analysis = result
                            st.success("Анализ сохранён.")
                            st.rerun()

                    except Exception as e:
                        st.error(f"Не удалось выполнить анализ: {e}")

        except Exception as e:
            st.error(f"Не удалось открыть изображение: {e}")

    if st.session_state.get("last_analysis"):
        st.subheader("Последний результат")
        st.markdown(st.session_state.last_analysis)

        if st.button("✨ Улучшить рекомендации"):
            user = get_user(user_id)
            price = PRICES["improve"]

            if user["balance"] < price:
                st.error(
                    f"Для улучшения нужно {price} ₽. "
                    "Недостаточно средств."
                )
            else:
                try:
                    with st.spinner("Улучшаем рекомендации..."):
                        improved = call_ai([{
                            "role": "user",
                            "content": (
                                "Улучши и конкретизируй рекомендации "
                                "стилиста ниже. Предложи сочетания, "
                                "цвета и практичные варианты. "
                                "Отвечай по-русски в Markdown.\n\n"
                                + st.session_state.last_analysis
                            ),
                        }])

                    if charge_user(
                        user_id, price, "Улучшение образа"
                    ):
                        st.session_state.last_analysis = improved

                        with get_db() as con:
                            con.execute(
                                """
                                UPDATE outfit_analyses
                                SET improved_result=?
                                WHERE id=(
                                    SELECT id FROM outfit_analyses
                                    WHERE user_id=?
                                    ORDER BY id DESC LIMIT 1
                                )
                                """,
                                (improved, user_id),
                            )

                        st.success("Рекомендации улучшены.")
                        st.rerun()
                    else:
                        st.error("Не удалось списать средства.")

                except Exception as e:
                    st.error(f"Не удалось улучшить рекомендации: {e}")

    st.divider()
    st.subheader("🗂️ Сохранённые анализы")

    analyses = get_analyses(user_id)

    if not analyses:
        st.info("Сохранённых анализов пока нет.")
    else:
        for row in analyses:
            with st.expander(
                f"{row['style']} · {row['created_at']}"
            ):
                if row["image"]:
                    st.image(
                        row["image"],
                        use_container_width=True,
                    )

                st.markdown(row["analysis"] or "")

                if row["improved_result"]:
                    st.markdown("### Улучшенные рекомендации")
                    st.markdown(row["improved_result"])

                if st.button(
                    "Удалить анализ",
                    key=f"delete_analysis_{row['id']}",
                ):
                    delete_analysis(row["id"], user_id)
                    st.rerun()


# =====================================================
# WARDROBE
# =====================================================

with tab_wardrobe:
    st.header("👗 Мой гардероб")
    st.write(
        "Добавляй вещи вручную или распознавай их через AI. "
        "Данные сохраняются в базе."
    )

    st.subheader("➕ Добавить вещь")

    with st.form("add_item_form", clear_on_submit=True):
        uploaded = st.file_uploader(
            "Фото одежды",
            type=["jpg", "jpeg", "png"],
            key="add_single_item",
        )

        item_name = st.text_input(
            "Название",
            placeholder="Например, чёрная куртка",
        )

        category = st.selectbox("Категория", CATEGORIES)
        color = st.text_input("Цвет")
        material = st.text_input("Материал")
        brand = st.text_input("Бренд")
        description = st.text_area("Описание")

        add_clicked = st.form_submit_button(
            "💾 Сохранить вещь",
            use_container_width=True,
        )

    if add_clicked:
        if not uploaded:
            st.error("Сначала загрузи фотографию.")
        else:
            try:
                photo = image_bytes(uploaded)

                add_wardrobe_item(
                    user_id,
                    item_name,
                    category,
                    color,
                    material,
                    brand,
                    description,
                    photo,
                    uploaded.name,
                )

                st.success("Вещь добавлена в гардероб.")
                st.rerun()

            except Exception as e:
                st.error(f"Не удалось сохранить вещь: {e}")

    # AI clothing recognition
    st.divider()
    st.subheader("✨ Распознать вещи с помощью AI")

    ai_files = st.file_uploader(
        "Загрузи до 6 фотографий",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="ai_wardrobe_files",
    )

    if ai_files:
        if len(ai_files) > 6:
            st.warning("Можно загрузить максимум 6 фотографий.")
        else:
            previews = st.columns(min(3, len(ai_files)))

            for i, file in enumerate(ai_files):
                with previews[i % len(previews)]:
                    st.image(
                        file,
                        caption=f"Фото {i + 1}",
                        use_container_width=True,
                    )

            if st.button(
                "🤖 Распознать и сохранить вещи",
                type="primary",
            ):
                try:
                    with st.spinner("AI распознаёт одежду..."):
                        photos = [image_bytes(f) for f in ai_files]

                        prompt = """
Определи одежду на каждой фотографии.
Верни только JSON:
{
  "items": [
    {
      "number": 1,
      "name": "название",
      "category": "категория",
      "color": "цвет",
      "material": "материал или фактура",
      "brand": "бренд или Не удалось определить",
      "description": "описание"
    }
  ]
}
Номера должны соответствовать порядку фотографий.
Не выдумывай бренды. Ответ на русском языке.
"""

                        content = [
                            {"type": "text", "text": prompt}
                        ]

                        for photo in photos:
                            content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": image_data_url(photo)
                                },
                            })

                        raw = call_ai(
                            [{"role": "user", "content": content}],
                            max_tokens=2000,
                        )

                        data = parse_json(raw)
                        items = data.get("items", [])
                        saved_count = 0

                        for item in items:
                            try:
                                number = int(item.get("number", 0))
                            except (ValueError, TypeError):
                                continue

                            if not 1 <= number <= len(photos):
                                continue

                            add_wardrobe_item(
                                user_id,
                                str(item.get("name", "Вещь")),
                                str(item.get("category", "Другое")),
                                str(item.get("color", "")),
                                str(item.get("material", "")),
                                str(item.get(
                                    "brand", "Не удалось определить"
                                )),
                                str(item.get("description", "")),
                                photos[number - 1],
                                ai_files[number - 1].name,
                            )

                            saved_count += 1

                    if saved_count:
                        st.success(
                            f"Добавлено вещей: {saved_count}."
                        )
                        st.rerun()
                    else:
                        st.warning(
                            "AI не распознал вещи. "
                            "Попробуй другие фотографии."
                        )

                except Exception as e:
                    st.error(f"Не удалось распознать одежду: {e}")

    # Saved wardrobe items
    st.divider()
    st.subheader("🧥 Мои сохранённые вещи")

    wardrobe = get_wardrobe_items(user_id)

    if not wardrobe:
        st.info("Гардероб пока пуст.")
    else:
        st.caption(f"Всего вещей: {len(wardrobe)}")
        cols = st.columns(3)

        for index, item in enumerate(wardrobe):
            with cols[index % 3]:
                st.image(
                    item["image"],
                    use_container_width=True,
                )
                st.markdown(f"**{item['name']}**")
                st.caption(item["category"] or "Категория не указана")

                if item["color"]:
                    st.write(f"🎨 Цвет: {item['color']}")
                if item["material"]:
                    st.write(f"Материал: {item['material']}")
                if item["brand"]:
                    st.write(f"Бренд: {item['brand']}")
                if item["description"]:
                    st.write(item["description"])

                with st.expander("Изменить данные"):
                    with st.form(f"edit_item_{item['id']}"):
                        new_name = st.text_input(
                            "Название", value=item["name"] or ""
                        )
                        new_category = st.text_input(
                            "Категория", value=item["category"] or ""
                        )
                        new_color = st.text_input(
                            "Цвет", value=item["color"] or ""
                        )
                        new_material = st.text_input(
                            "Материал", value=item["material"] or ""
                        )
                        new_brand = st.text_input(
                            "Бренд", value=item["brand"] or ""
                        )
                        new_description = st.text_area(
                            "Описание",
                            value=item["description"] or "",
                        )

                        update_clicked = st.form_submit_button(
                            "Сохранить изменения"
                        )

                    if update_clicked:
                        update_wardrobe_item(
                            item["id"],
                            user_id,
                            new_name,
                            new_category,
                            new_color,
                            new_material,
                            new_brand,
                            new_description,
                        )
                        st.success("Изменения сохранены.")
                        st.rerun()

                if st.button(
                    "🗑️ Удалить вещь",
                    key=f"delete_item_{item['id']}",
                    use_container_width=True,
                ):
                    delete_wardrobe_item(item["id"], user_id)
                    st.success("Вещь удалена.")
                    st.rerun()

    # Generate outfit from wardrobe
    st.divider()
    st.subheader("✨ Собрать образ из гардероба")

    wardrobe = get_wardrobe_items(user_id)

    if len(wardrobe) < 2:
        st.info("Добавь хотя бы две вещи, чтобы собрать образ.")
    else:
        free_outfit = not get_user(user_id)["free_outfit_used"]
        outfit_price = 0 if free_outfit else PRICES["outfit"]

        st.write(
            "Первая сборка бесплатна."
            if free_outfit
            else f"Стоимость сборки: {outfit_price} ₽"
        )

        selected_ids = st.multiselect(
            "Выбери вещи для образа",
            options=[item["id"] for item in wardrobe],
            format_func=lambda item_id: next(
                (
                    f"{item['name']} · {item['category']}"
                    for item in wardrobe
                    if item["id"] == item_id
                ),
                f"Вещь #{item_id}",
            ),
            default=[item["id"] for item in wardrobe[:min(6, len(wardrobe))]],
        )

        outfit_style = st.selectbox(
            "Стиль нового образа",
            STYLES,
            key="wardrobe_outfit_style",
        )

        if st.button("✨ Сгенерировать образ", type="primary"):
            if len(selected_ids) < 2:
                st.warning("Выбери минимум две вещи.")
            else:
                user = get_user(user_id)
                free_outfit = not user["free_outfit_used"]
                outfit_price = 0 if free_outfit else PRICES["outfit"]

                if not free_outfit and user["balance"] < outfit_price:
                    st.error("Недостаточно средств.")
                else:
                    selected_items = [
                        item for item in wardrobe
                        if item["id"] in selected_ids
                    ]

                    description = "\n".join(
                        f"- {item['name']}; категория: {item['category']}; "
                        f"цвет: {item['color']}; материал: {item['material']}; "
                        f"бренд: {item['brand']}; "
                        f"описание: {item['description']}"
                        for item in selected_items
                    )

                    try:
                        with st.spinner("Стилист собирает образ..."):
                            result = call_ai([{
                                "role": "user",
                                "content": f"""
Ты — профессиональный стилист.
Собери комплект из выбранных вещей в стиле
{outfit_style}.

Используй только перечисленные вещи.
Не придумывай одежду, которой нет в списке.
Объясни сочетаемость цветов и фактур.
Если нужно добавить другие вещи, укажи их отдельно
как необязательные дополнения.

Список вещей:
{description}

Ответь по-русски в Markdown.
Предложи название образа, объяснение сочетаний,
обувь и аксессуары, если они уже есть в списке.
"""
                            }])

                        if free_outfit:
                            if not use_free_attempt(
                                user_id, "free_outfit_used"
                            ):
                                raise RuntimeError(
                                    "Бесплатная попытка уже использована."
                                )
                        else:
                            if not charge_user(
                                user_id,
                                outfit_price,
                                "Сбор образа из гардероба",
                            ):
                                raise RuntimeError(
                                    "Не удалось списать средства."
                                )

                        save_outfit(
                            user_id,
                            f"Образ · {outfit_style}",
                            outfit_style,
                            selected_ids,
                            result,
                        )

                        st.success("Образ создан и сохранён.")
                        st.markdown(result)
                        st.rerun()

                    except Exception as e:
                        st.error(f"Не удалось собрать образ: {e}")

    # Saved outfits
    st.divider()
    st.subheader("⭐ Сохранённые образы")

    saved_outfits = get_saved_outfits(user_id)

    if not saved_outfits:
        st.info("Сохранённых образов пока нет.")
    else:
        for outfit in saved_outfits:
            with st.expander(
                f"{outfit['name']} · {outfit['created_at']}"
            ):
                st.caption(f"Стиль: {outfit['style'] or '—'}")

                try:
                    outfit_items = json.loads(
                        outfit["items_json"] or "[]"
                    )
                except (ValueError, TypeError):
                    outfit_items = []

                for item_id in outfit_items:
                    matching_item = next(
                        (
                            item for item in wardrobe
                            if item["id"] == item_id
                        ),
                        None,
                    )
                    if matching_item:
                        st.write(f"• {matching_item['name']}")

                st.markdown(outfit["explanation"] or "")

                if st.button(
                    "🗑️ Удалить образ",
                    key=f"delete_outfit_{outfit['id']}",
                ):
                    delete_saved_outfit(outfit["id"], user_id)
                    st.rerun()


# =====================================================
# ACCOUNT
# =====================================================

with tab_account:
    user = get_user(user_id)

    st.header("👤 Мой аккаунт")
    st.write(f"Email: **{user['email']}**")
    st.metric("Баланс", f"{user['balance']} ₽")

    st.subheader("🎁 Бесплатные попытки")

    st.write(
        "Оценка образа: "
        + (
            "доступна"
            if not user["free_analysis_used"]
            else "использована"
        )
    )

    # The database migration above guarantees this field exists.
    st.write(
        "Сбор образа из гардероба: "
        + (
            "доступен"
            if not user["free_outfit_used"]
            else "использован"
        )
    )

    st.subheader("💳 Цены")
    st.write(f"Оценка образа: {PRICES['analysis']} ₽")
    st.write(f"Улучшение образа: {PRICES['improve']} ₽")
    st.write(f"Сбор образа из гардероба: {PRICES['outfit']} ₽")

    st.info(
        "Реальное пополнение баланса пока не подключено. "
        "Для приёма платежей необходимо настроить платёжного "
        "провайдера и серверную проверку оплаты."
    )

    st.subheader("📋 История операций")

    with get_db() as con:
        transactions = con.execute(
            """
            SELECT * FROM transactions
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()

    if not transactions:
        st.caption("Операций пока нет.")
    else:
        for tx in transactions:
            sign = "+" if tx["amount"] > 0 else ""
            st.write(
                f"**{sign}{tx['amount']} ₽** · "
                f"{tx['description']} · {tx['created_at']}"
            )

    st.divider()

    st.button(
        "🚪 Выйти из аккаунта",
        on_click=logout,
        use_container_width=True,
    )


# =====================================================
# FOOTER
# =====================================================

st.divider()
st.caption(
    "AI Stylist · AI-анализ одежды · "
    "Персональный гардероб · Постоянное хранение данных"
)
