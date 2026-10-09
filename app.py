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
from PIL import Image

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

api_key = os.getenv("AITUNNEL_API_KEY")
client = None

if api_key:
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.aitunnel.ru/v1",
        timeout=180,
        max_retries=2,
    )

# =====================================================
# STYLE
# No HTML line-break tags are used in page content.
# =====================================================

st.markdown(
    """
    <style>
    #MainMenu {visibility:hidden;}
    footer {visibility:hidden;}
    header {visibility:hidden;}

    .block-container {
        max-width: 1180px;
        padding-top: 1.5rem;
        padding-bottom: 3rem;
    }

    .hero {
        padding: 28px;
        border-radius: 24px;
        background: linear-gradient(135deg,#f4f4f4,#ffffff);
        border: 1px solid #e8e8e8;
        margin-bottom: 20px;
    }

    .hero-title {
        font-size: 38px;
        font-weight: 850;
        letter-spacing: -1px;
    }

    .muted {color:#777;}
    </style>
    """,
    unsafe_allow_html=True,
)

# =====================================================
# DATABASE
# =====================================================

def get_db():
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


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
            user_id INTEGER NOT NULL,
            style TEXT NOT NULL,
            analysis TEXT NOT NULL,
            improved_result TEXT DEFAULT '',
            image BLOB,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS saved_outfits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            style TEXT DEFAULT '',
            items_json TEXT DEFAULT '[]',
            explanation TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
                ON DELETE CASCADE
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

        # Safe migrations for databases from the older version.
        columns = {
            r["name"]
            for r in con.execute(
                "PRAGMA table_info(outfit_analyses)"
            ).fetchall()
        }
        if "user_id" not in columns:
            con.execute(
                "ALTER TABLE outfit_analyses ADD COLUMN user_id INTEGER"
            )
        if "improved_result" not in columns:
            con.execute(
                "ALTER TABLE outfit_analyses "
                "ADD COLUMN improved_result TEXT DEFAULT ''"
            )
        if "image" not in columns:
            con.execute(
                "ALTER TABLE outfit_analyses ADD COLUMN image BLOB"
            )

        columns = {
            r["name"]
            for r in con.execute(
                "PRAGMA table_info(saved_outfits)"
            ).fetchall()
        }
        if "user_id" not in columns:
            con.execute(
                "ALTER TABLE saved_outfits ADD COLUMN user_id INTEGER"
            )
        if "items_json" not in columns:
            con.execute(
                "ALTER TABLE saved_outfits "
                "ADD COLUMN items_json TEXT DEFAULT '[]'"
            )
        if "explanation" not in columns:
            con.execute(
                "ALTER TABLE saved_outfits "
                "ADD COLUMN explanation TEXT DEFAULT ''"
            )


init_database()

# =====================================================
# HELPERS
# =====================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
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
            actual,
            base64.b64decode(digest),
        )
    except (ValueError, TypeError):
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
    """Normalize uploaded photos to JPEG to reduce storage size."""
    img = Image.open(uploaded_file).convert("RGB")
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
            "Добавь ключ в переменные окружения и перезапусти приложение."
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
        raise ValueError("AI вернул ответ в неправильном формате JSON.")


# =====================================================
# WARDROBE DATABASE OPERATIONS
# =====================================================

def get_wardrobe_items(user_id):
    with get_db() as con:
        return con.execute(
            """
            SELECT *
            FROM wardrobe_items
            WHERE user_id=?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()


def add_wardrobe_item(
    user_id,
    name,
    category,
    color,
    material,
    brand,
    description,
    photo,
    photo_name="photo.jpg",
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


def save_outfit(user_id, name, style, items, explanation):
    with get_db() as con:
        cur = con.execute(
            """
            INSERT INTO saved_outfits
            (user_id, name, style, items_json, explanation, created_at)
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
            "SELECT * FROM saved_outfits WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        ).fetchall()


def delete_saved_outfit(outfit_id, user_id):
    with get_db() as con:
        con.execute(
            "DELETE FROM saved_outfits WHERE id=? AND user_id=?",
            (outfit_id, user_id),
        )


def get_analyses(user_id):
    with get_db() as con:
        return con.execute(
            "SELECT * FROM outfit_analyses WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        ).fetchall()


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


def delete_analysis(analysis_id, user_id):
    with get_db() as con:
        con.execute(
            "DELETE FROM outfit_analyses WHERE id=? AND user_id=?",
            (analysis_id, user_id),
        )


def charge_user(user_id, amount, description):
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
    # column is chosen only from this fixed allowlist.
    if column not in ("free_analysis_used", "free_outfit_used"):
        raise ValueError("Invalid free-attempt field.")

    with get_db() as con:
        cur = con.execute(
            f"UPDATE users SET {column}=1 WHERE id=? AND {column}=0",
            (user_id,),
        )
        return cur.rowcount == 1


# =====================================================
# SESSION
# =====================================================

if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "selected_style" not in st.session_state:
    st.session_state.selected_style = STYLES[0]


def logout():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# =====================================================
# LOGIN / REGISTER
# =====================================================

if not st.session_state.user_id:
    st.markdown(
        """
        <div class="hero">
            <div class="hero-title">👕 AI Stylist</div>
            <div>Твой персональный AI-стилист</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    login_tab, register_tab = st.tabs(["🔑 Войти", "✨ Регистрация"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button(
                "Войти",
                use_container_width=True,
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
            new_email = st.text_input("Email", key="register_email")
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
                ok, result = register_user(new_email, new_password)
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
        <div>Собирай образы, анализируй стиль и управляй гардеробом</div>
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
    st.button("Выйти", on_click=logout, use_container_width=True)

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
            st.image(photo, caption="Твой образ", use_container_width=True)

            free_available = user["free_analysis_used"] == 0
            price = 0 if free_available else PRICES["analysis"]
            st.write(
                "Первая оценка бесплатна."
                if free_available
                else f"Стоимость анализа: {price} ₽"
            )

            if st.button("✨ Проанализировать образ", type="primary"):
                if not free_available and user["balance"] < price:
                    st.error("Недостаточно средств.")
                else:
                    with st.spinner("AI анализирует образ..."):
                        try:
                            prompt = f"""
Ты — профессиональный стилист.
Проанализируй только одежду, обувь, аксессуары, цвета,
сочетания и соответствие стилю {st.session_state.selected_style}.
Не оценивай лицо, тело или привлекательность.
Отвечай по-русски, используй Markdown, нормальные переносы строк.

ОЦЕНКА: X/10
### Что хорошо
- ...
### Что улучшить
- ...
### Что добавить
- ...
### Совет
...
"""
                            result = call_ai(
                                [{
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
                                }],
                            )

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
                    st.image(row["image"], use_container_width=True)
                st.markdown(row["analysis"])
                if row["improved_result"]:
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
        "Вещи сохраняются в базе данных и останутся после "
        "выхода из аккаунта и перезапуска приложения."
    )

    st.subheader("➕ Добавить вещь")

    with st.form("add_item_form", clear_on_submit=True):
        uploaded = st.file_uploader(
            "Фото одежды",
            type=["jpg", "jpeg", "png"],
            key="add_single_item",
        )
        item_name = st.text_input("Название", placeholder="Например, чёрная куртка")
        category = st.selectbox(
            "Категория",
            [
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
            ],
        )
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
                st.success("Вещь добавлена в твой гардероб.")
                st.rerun()
            except Exception as e:
                st.error(f"Не удалось сохранить вещь: {e}")

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
            st.warning("Можно загрузить максимум 6 фотографий за раз.")
        else:
            previews = st.columns(min(3, len(ai_files)))
            for i, file in enumerate(ai_files):
                with previews[i % len(previews)]:
                    st.image(file, caption=f"Фото {i + 1}", use_container_width=True)

            if st.button("🤖 Распознать и сохранить вещи", type="primary"):
                with st.spinner("AI распознаёт одежду..."):
                    try:
                        photos = [image_bytes(f) for f in ai_files]
                        prompt = """
Определи каждую одежду на фотографиях.
Верни только JSON следующего вида:
{
  "items": [
    {
      "number": 1,
      "name": "название вещи",
      "category": "категория",
      "color": "цвет",
      "material": "материал или фактура",
      "brand": "бренд или Не удалось определить",
      "description": "краткое описание"
    }
  ]
}
Номера соответствуют порядку фотографий.
Не выдумывай бренды. Ответ на русском языке.
"""
                        content = [{"type": "text", "text": prompt}]
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
                                str(item.get("category", "")),
                                str(item.get("color", "")),
                                str(item.get("material", "")),
                                str(item.get("brand", "Не удалось определить")),
                                str(item.get("description", "")),
                                photos[number - 1],
                                ai_files[number - 1].name,
                            )
                            saved_count += 1

                        if saved_count:
                            st.success(
                                f"В гардероб добавлено вещей: {saved_count}."
                            )
                            st.rerun()
                        else:
                            st.warning(
                                "AI не вернул распознаваемые вещи. "
                                "Попробуй другие фотографии."
                            )

                    except Exception as e:
                        st.error(f"Не удалось распознать одежду: {e}")

    st.divider()
    st.subheader("🧥 Мои сохранённые вещи")

    wardrobe = get_wardrobe_items(user_id)

    if not wardrobe:
        st.info("Гардероб пока пуст. Добавь первую вещь выше.")
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
                            "Название",
                            value=item["name"] or "",
                        )
                        new_category = st.text_input(
                            "Категория",
                            value=item["category"] or "",
                        )
                        new_color = st.text_input(
                            "Цвет",
                            value=item["color"] or "",
                        )
                        new_material = st.text_input(
                            "Материал",
                            value=item["material"] or "",
                        )
                        new_brand = st.text_input(
                            "Бренд",
                            value=item["brand"] or "",
                        )
                        new_description = st.text_area(
                            "Описание",
                            value=item["description"] or "",
                        )
                        update_clicked = st.form_submit_button(
                            "Сохранить изменения"
                        )

                    if update_clicked:
                        with get_db() as con:
                            con.execute(
                                """
                                UPDATE wardrobe_items
                                SET name=?, category=?, color=?,
                                    material=?, brand=?, description=?
                                WHERE id=? AND user_id=?
                                """,
                                (
                                    new_name.strip() or "Вещь",
                                    new_category,
                                    new_color,
                                    new_material,
                                    new_brand,
                                    new_description,
                                    item["id"],
                                    user_id,
                                ),
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
                    outfit_items = json.loads(outfit["items_json"] or "[]")
                except (ValueError, TypeError):
                    outfit_items = []
                if outfit_items:
                    st.write("ID вещей: " + ", ".join(map(str, outfit_items)))
                if outfit["explanation"]:
                    st.write(outfit["explanation"])
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
        + ("доступна" if not user["free_analysis_used"] else "использована")
    )
    st.write(
        "Сбор образа из гардероба: "
        + ("доступен" if not user["free_outfit_used"] else "использован")
    )

    st.subheader("💳 Цены")
    st.write(f"Оценка образа: {PRICES['analysis']} ₽")
    st.write(f"Улучшение образа: {PRICES['improve']} ₽")
    st.write(f"Сбор образов из гардероба: {PRICES['outfit']} ₽")

    st.info(
        "Пополнение баланса не подключено. "
        "Для реальных платежей потребуется платёжный провайдер."
    )

    st.subheader("📋 История операций")
    with get_db() as con:
        transactions = con.execute(
            """
            SELECT * FROM transactions
            WHERE user_id=?
            ORDER BY id DESC LIMIT 50
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
