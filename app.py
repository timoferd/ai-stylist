import os
import json
import base64
import re
import sqlite3

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
    layout="centered"
)

# Получаем API-ключ:
# локально — из .env
# на сервере Coolify — из Environment Variables
api_key = os.getenv("AITUNNEL_API_KEY")

if not api_key:
    st.error("❌ API-ключ AITUNNEL_API_KEY не найден.")
    st.info(
        "Добавь переменную AITUNNEL_API_KEY "
        "в Environment Variables в Coolify."
    )
    st.stop()


client = OpenAI(
    api_key=api_key,
    base_url="https://api.aitunnel.ru/v1",
    timeout=300.0,
    max_retries=3
)


MODEL = "gpt-5.6-sol"

client = OpenAI(
    api_key=api_key,
    base_url="https://api.aitunnel.ru/v1",
    timeout=600.0,
    max_retries=2
)


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

DB_FILE = "ai_stylist.db"


def get_db():
    """
    Подключение к SQLite.
    """

    connection = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database():
    """
    Создаёт таблицы при первом запуске.
    """

    connection = get_db()

    cursor = connection.cursor()

    # -----------------------------------------------------
    # Анализы образов
    # -----------------------------------------------------

    cursor.execute(
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

    # -----------------------------------------------------
    # Сохранённые образы гардероба
    # -----------------------------------------------------

    cursor.execute(
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

    connection.commit()

    connection.close()


init_database()


# =========================================================
# DATABASE — АНАЛИЗЫ
# =========================================================

def save_analysis_to_db(
    style,
    analysis,
    image_bytes=None,
    image_name=None,
    image_type=None
):
    """
    Сохраняет анализ и фотографию в SQLite.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO outfit_analyses
        (
            style,
            analysis,
            improved_result,
            image,
            image_name,
            image_type,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            style,
            analysis,
            None,
            image_bytes,
            image_name,
            image_type,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    connection.commit()

    analysis_id = cursor.lastrowid

    connection.close()

    return analysis_id


def update_analysis_improvement(
    analysis_id,
    improved_result
):
    """
    Сохраняет улучшенную версию
    существующего анализа.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE outfit_analyses
        SET improved_result = ?
        WHERE id = ?
        """,
        (
            improved_result,
            analysis_id
        )
    )

    connection.commit()

    connection.close()


def get_all_analyses():
    """
    Возвращает все сохранённые анализы.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM outfit_analyses
        ORDER BY id DESC
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


def get_analysis(analysis_id):
    """
    Возвращает один анализ.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM outfit_analyses
        WHERE id = ?
        """,
        (analysis_id,)
    )

    row = cursor.fetchone()

    connection.close()

    return row


def delete_analysis(analysis_id):
    """
    Удаляет анализ.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM outfit_analyses
        WHERE id = ?
        """,
        (analysis_id,)
    )

    connection.commit()

    connection.close()


def analysis_exists(
    style,
    analysis
):
    """
    Проверяет, сохранён ли уже такой анализ.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM outfit_analyses
        WHERE style = ?
        AND analysis = ?
        LIMIT 1
        """,
        (
            style,
            analysis
        )
    )

    row = cursor.fetchone()

    connection.close()

    return row is not None


# =========================================================
# DATABASE — СОХРАНЁННЫЕ ОБРАЗЫ
# =========================================================

def save_wardrobe_outfit(
    name,
    style,
    items,
    explanation
):
    """
    Сохраняет образ из гардероба.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO saved_outfits
        (
            name,
            style,
            items_json,
            explanation,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name,
            style,
            json.dumps(
                items,
                ensure_ascii=False
            ),
            explanation,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    connection.commit()

    connection.close()


def get_saved_wardrobe_outfits():
    """
    Возвращает сохранённые образы гардероба.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM saved_outfits
        ORDER BY id DESC
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


def delete_saved_wardrobe_outfit(
    outfit_id
):
    """
    Удаляет сохранённый образ гардероба.
    """

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM saved_outfits
        WHERE id = ?
        """,
        (outfit_id,)
    )

    connection.commit()

    connection.close()


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def image_to_data_url(uploaded_file):

    image_data = base64.b64encode(
        uploaded_file.getvalue()
    ).decode("utf-8")

    mime_type = (
        uploaded_file.type
        or "image/jpeg"
    )

    return (
        f"data:{mime_type};"
        f"base64,{image_data}"
    )


def extract_json(text):

    if not text:
        raise ValueError(
            "AI вернул пустой ответ."
        )

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    text = text.strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:

        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end != -1:

            return json.loads(
                text[start:end + 1]
            )

        raise


def safe_int(value):

    try:
        return int(value)

    except (
        TypeError,
        ValueError
    ):
        return None


# =========================================================
# SESSION STATE
# =========================================================

if "selected_style" not in st.session_state:

    st.session_state.selected_style = (
        "Indie Sleaze"
    )


if "analysis_result" not in st.session_state:

    st.session_state.analysis_result = None


if "analysis_style" not in st.session_state:

    st.session_state.analysis_style = None


if "improved_result" not in st.session_state:

    st.session_state.improved_result = None


if "current_analysis_id" not in st.session_state:

    st.session_state.current_analysis_id = None


if "wardrobe_data" not in st.session_state:

    st.session_state.wardrobe_data = None


# =========================================================
# CSS
# =========================================================

st.markdown(
    """
    <style>

    .main-title {
        text-align: center;
        font-size: 42px;
        font-weight: 800;
        margin-bottom: 5px;
    }

    .subtitle {
        text-align: center;
        color: #777;
        margin-bottom: 30px;
    }

    .small-card {
        padding: 15px;
        border-radius: 14px;
        border: 1px solid rgba(128,128,128,0.25);
        margin-bottom: 12px;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# ЗАГОЛОВОК
# =========================================================

st.markdown(
    '<div class="main-title">👕 AI Stylist</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Твой персональный AI-стилист'
    '</div>',
    unsafe_allow_html=True
)


# =========================================================
# СТИЛИ
# =========================================================

STYLES = [
    "Indie Sleaze",
    "Drip / Streetwear",
    "Old Money",
    "Y2K",
    "Minimalism"
]


st.header("🎨 Выбери свой стиль")


style_columns = st.columns(2)

for i, style_name in enumerate(
    STYLES
):

    if i < 4:

        column = style_columns[
            i % 2
        ]

    else:

        column = st.columns(1)[0]

    with column:

        if st.button(
            style_name,
            use_container_width=True,
            key=f"style_{i}"
        ):

            st.session_state.selected_style = (
                style_name
            )


style = st.session_state.selected_style


st.success(
    f"🎨 Выбран стиль: **{style}**"
)


# =========================================================
# АНАЛИЗ ОБРАЗА
# =========================================================

st.divider()

st.header("📸 Анализ твоего образа")


uploaded_file = st.file_uploader(
    "Выбери фотографию образа",
    type=[
        "jpg",
        "jpeg",
        "png"
    ],
    key="outfit_uploader"
)


if uploaded_file:

    image_bytes = uploaded_file.getvalue()

    st.image(
        image_bytes,
        caption="Твой образ",
        width="stretch"
    )

    st.success(
        "Фото загружено! ✅"
    )


    # =====================================================
    # АНАЛИЗ
    # =====================================================

    if st.button(
        "✨ Проанализировать мой образ",
        use_container_width=True,
        key="analyze_outfit"
    ):

        with st.spinner(
            "AI анализирует твой образ..."
        ):

            try:

                image_url = image_to_data_url(
                    uploaded_file
                )

                prompt = f"""
Ты — профессиональный AI-стилист.

Пользователь выбрал стиль:

{style}

Проанализируй одежду человека
на фотографии с точки зрения
соответствия стилю "{style}".

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

Короткий конкретный совет,
как улучшить образ.

Анализируй только:

- одежду;
- цвета;
- вещи;
- обувь;
- аксессуары;
- сочетания;
- силуэт;
- пропорции;
- соответствие выбранному стилю.

Не оценивай:

- лицо;
- тело;
- привлекательность;
- физические особенности человека.
"""

                response = (
                    client
                    .chat
                    .completions
                    .create(
                        model=MODEL,
                        max_tokens=1600,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": prompt
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": image_url
                                        }
                                    }
                                ]
                            }
                        ]
                    )
                )

                result = (
                    response
                    .choices[0]
                    .message
                    .content
                )

                if not result:

                    raise ValueError(
                        "AI вернул пустой результат."
                    )

                st.session_state.analysis_result = (
                    result
                )

                st.session_state.analysis_style = (
                    style
                )

                st.session_state.improved_result = (
                    None
                )

                st.session_state.current_analysis_id = (
                    None
                )

                st.success(
                    "Анализ готов! ✅"
                )

            except Exception as e:

                error_text = str(e)

                if (
                    "timeout"
                    in error_text.lower()
                    or "connecttimeout"
                    in error_text.lower()
                ):

                    st.error(
                        "⏱️ Не удалось дождаться ответа AI."
                    )

                    st.info(
                        "Проверь интернет-соединение "
                        "и попробуй ещё раз. "
                        "Также можно загрузить фотографию "
                        "меньшего размера."
                    )

                else:

                    st.error(
                        "❌ Ошибка при анализе."
                    )

                st.code(
                    error_text
                )


# =========================================================
# РЕЗУЛЬТАТ АНАЛИЗА
# =========================================================

if st.session_state.analysis_result:

    st.divider()

    st.header(
        "🤖 Результат анализа"
    )

    analysis_style = (
        st.session_state.analysis_style
        or style
    )

    st.info(
        f"🎨 Стиль: **{analysis_style}**"
    )

    st.markdown(
        st.session_state.analysis_result
    )


    # =====================================================
    # СОХРАНЕНИЕ АНАЛИЗА
    # =====================================================

    st.divider()

    st.subheader(
        "💾 Сохранить результат"
    )

    st.write(
        "Сохрани анализ, чтобы он "
        "не исчез после перезапуска приложения."
    )


    if st.session_state.current_analysis_id:

        st.success(
            "⭐ Этот анализ уже сохранён в базе данных."
        )

    else:

        if st.button(
            "💾 Сохранить анализ",
            use_container_width=True,
            key="save_analysis"
        ):

            try:

                # Проверяем дубликат
                if analysis_exists(
                    analysis_style,
                    st.session_state.analysis_result
                ):

                    st.warning(
                        "Этот анализ уже есть "
                        "в сохранённых."
                    )

                else:

                    saved_id = save_analysis_to_db(
                        style=analysis_style,
                        analysis=(
                            st.session_state.analysis_result
                        ),
                        image_bytes=(
                            uploaded_file.getvalue()
                            if uploaded_file
                            else None
                        ),
                        image_name=(
                            uploaded_file.name
                            if uploaded_file
                            else None
                        ),
                        image_type=(
                            uploaded_file.type
                            if uploaded_file
                            else None
                        )
                    )

                    st.session_state.current_analysis_id = (
                        saved_id
                    )

                    st.success(
                        "⭐ Анализ сохранён навсегда!"
                    )

            except Exception as e:

                st.error(
                    "❌ Не удалось сохранить анализ."
                )

                st.code(
                    str(e)
                )


    # =====================================================
    # УЛУЧШЕНИЕ
    # =====================================================

    st.divider()

    st.subheader(
        "✨ Улучшение образа"
    )


    if st.button(
        "✨ Улучшить мой образ",
        use_container_width=True,
        key="improve_outfit"
    ):

        with st.spinner(
            "AI разрабатывает улучшенную версию..."
        ):

            try:

                improve_prompt = f"""
Ты — профессиональный AI-стилист.

Пользователь выбрал стиль:

{analysis_style}

Вот анализ текущего образа:

{st.session_state.analysis_result}

Составь конкретный план улучшения.

Ответь на русском языке.

Используй формат:

🎯 ГЛАВНАЯ ПРОБЛЕМА

Что сильнее всего можно улучшить.

👕 ЧТО ИЗМЕНИТЬ

- конкретная вещь
- конкретная вещь
- конкретная вещь

➕ ЧТО ДОБАВИТЬ

- конкретный предмет
- конкретный предмет
- конкретный аксессуар

🎨 ЦВЕТА

Какие цвета лучше использовать.

📐 ПРОПОРЦИИ

Как улучшить сочетание
верха, низа и обуви.

🔥 ГОТОВЫЙ ВАРИАНТ

Опиши итоговый образ.

💡 ГЛАВНЫЙ СОВЕТ

Один самый практичный совет.

Анализируй только одежду,
обувь и аксессуары.

Не оценивай лицо,
тело или привлекательность.
"""

                improve_response = (
                    client
                    .chat
                    .completions
                    .create(
                        model=MODEL,
                        max_tokens=1600,
                        messages=[
                            {
                                "role": "user",
                                "content": improve_prompt
                            }
                        ]
                    )
                )

                improved_result = (
                    improve_response
                    .choices[0]
                    .message
                    .content
                )

                if not improved_result:

                    raise ValueError(
                        "AI вернул пустой результат."
                    )

                st.session_state.improved_result = (
                    improved_result
                )

                # Если анализ уже сохранён —
                # сохраняем улучшение тоже
                if (
                    st.session_state.current_analysis_id
                ):

                    update_analysis_improvement(
                        st.session_state.current_analysis_id,
                        improved_result
                    )

                st.success(
                    "✨ Улучшенная версия готова!"
                )

            except Exception as e:

                st.error(
                    "❌ Не удалось улучшить образ."
                )

                st.code(
                    str(e)
                )


    if st.session_state.improved_result:

        st.divider()

        st.header(
            "✨ Улучшенная версия"
        )

        st.markdown(
            st.session_state.improved_result
        )


# =========================================================
# СОХРАНЁННЫЕ АНАЛИЗЫ
# =========================================================

st.divider()

st.header(
    "🗂️ Сохранённые анализы"
)

saved_analyses = get_all_analyses()


if not saved_analyses:

    st.info(
        "Пока нет сохранённых анализов."
    )

else:

    st.write(
        f"Всего сохранено: **{len(saved_analyses)}**"
    )


    for saved in saved_analyses:

        analysis_id = saved["id"]

        title = (
            f"👕 {saved['style']} "
            f"• {saved['created_at']}"
        )

        with st.expander(
            title,
            expanded=False
        ):

            # ---------------------------------------------
            # ФОТО
            # ---------------------------------------------

            if saved["image"]:

                st.image(
                    saved["image"],
                    caption=(
                        saved["image_name"]
                        or "Сохранённый образ"
                    ),
                    width="stretch"
                )


            # ---------------------------------------------
            # СТИЛЬ
            # ---------------------------------------------

            st.info(
                f"🎨 Стиль: **{saved['style']}**"
            )


            # ---------------------------------------------
            # АНАЛИЗ
            # ---------------------------------------------

            st.markdown(
                saved["analysis"]
            )


            # ---------------------------------------------
            # УЛУЧШЕНИЕ
            # ---------------------------------------------

            if saved["improved_result"]:

                st.divider()

                st.subheader(
                    "✨ Улучшенная версия"
                )

                st.markdown(
                    saved["improved_result"]
                )


            # ---------------------------------------------
            # УДАЛЕНИЕ
            # ---------------------------------------------

            st.divider()

            if st.button(
                "🗑️ Удалить анализ",
                key=f"delete_analysis_{analysis_id}",
                use_container_width=True
            ):

                delete_analysis(
                    analysis_id
                )

                # Если удалили текущий анализ
                if (
                    st.session_state.current_analysis_id
                    == analysis_id
                ):

                    st.session_state.current_analysis_id = (
                        None
                    )

                st.success(
                    "Анализ удалён."
                )

                st.rerun()


# =========================================================
# МОЙ ГАРДЕРОБ
# =========================================================

st.divider()

st.header(
    "👕 Мой гардероб"
)

st.write(
    "Загрузи фотографии своих вещей, "
    "и AI соберёт из них образы."
)


wardrobe_files = st.file_uploader(
    "Фотографии вещей",
    type=[
        "jpg",
        "jpeg",
        "png"
    ],
    accept_multiple_files=True,
    key="wardrobe_uploader"
)


if wardrobe_files:

    if len(wardrobe_files) > 6:

        st.warning(
            "⚠️ Максимум 6 фотографий."
        )

    else:

        st.success(
            f"Загружено вещей: "
            f"{len(wardrobe_files)} ✅"
        )


        # -------------------------------------------------
        # ПОКАЗ ВЕЩЕЙ
        # -------------------------------------------------

        cols = st.columns(3)

        for i, wardrobe_file in enumerate(
            wardrobe_files
        ):

            with cols[i % 3]:

                st.image(
                    wardrobe_file,
                    caption=f"Вещь №{i + 1}",
                    width="stretch"
                )


        st.divider()


        # -------------------------------------------------
        # СОБРАТЬ ОБРАЗЫ
        # -------------------------------------------------

        if st.button(
            "✨ Собрать образы",
            use_container_width=True,
            key="build_wardrobe"
        ):

            with st.spinner(
                "AI анализирует гардероб..."
            ):

                try:

                    wardrobe_prompt = f"""
Ты — профессиональный AI-стилист.

Пользователь выбрал стиль:

{style}

Пользователь загрузил
{len(wardrobe_files)} фотографий.

Фото 1 = вещь 1.
Фото 2 = вещь 2.
Фото 3 = вещь 3.
И так далее.

Проанализируй каждую вещь.

Для каждой определи:

- название;
- категорию;
- цвет;
- материал или фактуру;
- бренд;
- уверенность бренда.

НИКОГДА НЕ ПРИДУМЫВАЙ БРЕНД.

Если бренд не виден,
напиши:

"Не удалось определить"

После анализа создай
3 разных образа.

Используй ТОЛЬКО загруженные вещи.

Не добавляй вещи,
которых нет на фотографиях.

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

Номера вещей должны соответствовать
номерам фотографий.

Не анализируй лицо,
тело или привлекательность.
"""


                    content = [
                        {
                            "type": "text",
                            "text": wardrobe_prompt
                        }
                    ]


                    for wardrobe_file in wardrobe_files:

                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_to_data_url(
                                        wardrobe_file
                                    )
                                }
                            }
                        )


                    response = (
                        client
                        .chat
                        .completions
                        .create(
                            model=MODEL,
                            max_tokens=3000,
                            messages=[
                                {
                                    "role": "user",
                                    "content": content
                                }
                            ]
                        )
                    )


                    wardrobe_text = (
                        response
                        .choices[0]
                        .message
                        .content
                    )


                    wardrobe_data = extract_json(
                        wardrobe_text
                    )


                    if not isinstance(
                        wardrobe_data,
                        dict
                    ):

                        raise ValueError(
                            "Неверный формат JSON."
                        )


                    st.session_state.wardrobe_data = (
                        wardrobe_data
                    )

                    st.success(
                        "🎉 Гардероб готов!"
                    )


                except Exception as e:

                    error_text = str(e)

                    if (
                        "timeout"
                        in error_text.lower()
                    ):

                        st.error(
                            "⏱️ AI не успел ответить."
                        )

                    else:

                        st.error(
                            "❌ Ошибка анализа гардероба."
                        )

                    st.code(
                        error_text
                    )


# =========================================================
# ПОКАЗ ГАРДЕРОБА
# =========================================================

if st.session_state.wardrobe_data:

    wardrobe_data = (
        st.session_state.wardrobe_data
    )


    st.divider()

    st.header(
        "✨ Твой AI-гардероб"
    )


    items = wardrobe_data.get(
        "items",
        []
    )


    # -----------------------------------------------------
    # ВЕЩИ
    # -----------------------------------------------------

    st.subheader(
        "👕 Распознанные вещи"
    )


    item_by_number = {}


    for item in items:

        number = safe_int(
            item.get("number")
        )

        if number:

            item_by_number[number] = item


    if items:

        cols = st.columns(2)

        for index, item in enumerate(
            items
        ):

            with cols[index % 2]:

                st.markdown(
                    f"### 👕 "
                    f"{item.get('name', 'Вещь')}"
                )

                st.caption(
                    f"Вещь №{item.get('number', '?')}"
                )

                st.write(
                    f"**Категория:** "
                    f"{item.get('category', '—')}"
                )

                st.write(
                    f"**Цвет:** "
                    f"{item.get('color', '—')}"
                )

                st.write(
                    f"**Материал:** "
                    f"{item.get('material', '—')}"
                )

                st.write(
                    f"**Бренд:** "
                    f"{item.get('brand', '—')}"
                )

                st.write(
                    f"**Уверенность:** "
                    f"{item.get('brand_confidence', '—')}"
                )

                if item.get("description"):

                    st.caption(
                        item["description"]
                    )


    # -----------------------------------------------------
    # ОБРАЗЫ
    # -----------------------------------------------------

    st.subheader(
        "🔥 Образы"
    )


    outfits = wardrobe_data.get(
        "outfits",
        []
    )


    for outfit_index, outfit in enumerate(
        outfits
    ):

        outfit_number = outfit.get(
            "number",
            outfit_index + 1
        )

        outfit_name = outfit.get(
            "name",
            "Образ"
        )

        st.markdown(
            f"## 🔥 {outfit_name}"
        )


        outfit_items = outfit.get(
            "items",
            []
        )


        selected_items = []

        for number in outfit_items:

            number = safe_int(number)

            if number in item_by_number:

                selected_items.append(
                    item_by_number[number]
                )


        if selected_items:

            outfit_cols = st.columns(
                min(
                    len(selected_items),
                    3
                )
            )


            for index, item in enumerate(
                selected_items
            ):

                with outfit_cols[
                    index % len(outfit_cols)
                ]:

                    number = safe_int(
                        item.get("number")
                    )

                    if (
                        wardrobe_files
                        and number
                        and number <= len(
                            wardrobe_files
                        )
                    ):

                        st.image(
                            wardrobe_files[
                                number - 1
                            ],
                            width="stretch"
                        )

                    st.markdown(
                        f"**{item.get('name', 'Вещь')}**"
                    )

                    st.caption(
                        item.get(
                            "category",
                            ""
                        )
                    )


        explanation = outfit.get(
            "explanation",
            ""
        )


        if explanation:

            st.info(
                f"💡 {explanation}"
            )


        # -------------------------------------------------
        # СОХРАНЕНИЕ ОБРАЗА
        # -------------------------------------------------

        if st.button(
            "💾 Сохранить образ",
            use_container_width=True,
            key=f"save_wardrobe_{outfit_index}"
        ):

            try:

                save_wardrobe_outfit(
                    name=outfit_name,
                    style=style,
                    items=outfit_items,
                    explanation=explanation
                )

                st.success(
                    "⭐ Образ сохранён!"
                )

            except Exception as e:

                st.error(
                    "❌ Не удалось сохранить образ."
                )

                st.code(
                    str(e)
                )


        st.divider()


# =========================================================
# СОХРАНЁННЫЕ ОБРАЗЫ ГАРДЕРОБА
# =========================================================

st.header(
    "⭐ Сохранённые образы"
)


saved_wardrobe = (
    get_saved_wardrobe_outfits()
)


if not saved_wardrobe:

    st.info(
        "Сохранённых образов пока нет."
    )

else:

    for saved in saved_wardrobe:

        with st.expander(
            f"⭐ {saved['name']} "
            f"• {saved['created_at']}"
        ):

            st.write(
                f"🎨 Стиль: **{saved['style']}**"
            )


            try:

                saved_items = json.loads(
                    saved["items_json"]
                )

            except Exception:

                saved_items = []


            st.write(
                "Вещи: "
                + ", ".join(
                    f"№{x}"
                    for x in saved_items
                )
            )


            if saved["explanation"]:

                st.info(
                    saved["explanation"]
                )


            if st.button(
                "🗑️ Удалить образ",
                key=f"delete_wardrobe_{saved['id']}"
            ):

                delete_saved_wardrobe_outfit(
                    saved["id"]
                )

                st.success(
                    "Образ удалён."
                )

                st.rerun()


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "👕 AI Stylist • "
    "AI-анализ одежды • "
    "Постоянное хранение данных"
)
