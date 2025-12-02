import streamlit as st
import sqlite3
import hashlib
import datetime
import base64
import os
from typing import List
from openai import OpenAI

# -------------------- CONFIG --------------------
st.set_page_config(
    page_title="Calm Learning Hub",
    layout="wide"
)

client = OpenAI()  # uses OPENAI_API_KEY from environment

# -------------------- DB HELPERS --------------------
def get_connection():
    conn = sqlite3.connect("learning_app.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_connection()

def init_db():
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS teacher_learners (
        teacher_id INTEGER,
        learner_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS parent_children (
        parent_id INTEGER,
        learner_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        owner_role TEXT,
        title TEXT,
        original_text TEXT,
        friendly_text TEXT,
        image_b64 TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS lesson_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_id INTEGER,
        learner_id INTEGER,
        status TEXT,
        progress_step INTEGER,
        completed_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS help_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id INTEGER,
        to_user_id INTEGER,
        message TEXT,
        created_at TEXT,
        resolved INTEGER DEFAULT 0
    )
    """)

    conn.commit()

init_db()

# -------------------- BASIC UTILS --------------------
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def check_password(pw: str, hashed: str) -> bool:
    return hash_password(pw) == hashed

def split_into_steps(text: str, sentences_per_step: int = 1) -> List[str]:
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    steps = []
    for i in range(0, len(sentences), sentences_per_step):
        part = ". ".join(sentences[i:i + sentences_per_step]).strip()
        if part and not part.endswith("."):
            part += "."
        if part:
            steps.append(part)
    return steps

# -------------------- OPENAI HELPERS --------------------
def generate_friendly_text_with_openai(original: str, mode: str = "chapter") -> str:
    """
    Use OpenAI to turn a long chapter/story into
    a gentle, step-by-step lesson with short sentences.

    IMPORTANT: We avoid any mention of autism or special needs.
    """
    if not original.strip():
        return ""

    style_hint = "chapter from school" if mode == "chapter" else "short life story"

    system_msg = (
        "You are an expert in creating calm, step-by-step learning material for children.\n"
        "You always:\n"
        "- Use very clear and simple language.\n"
        "- Break content into short numbered steps.\n"
        "- Make each step 1–3 short sentences.\n"
        "- Keep the tone gentle, encouraging, and predictable.\n"
        "- DO NOT mention autism, disorders, or special needs.\n"
    )

    user_msg = (
        f"Turn the following {style_hint} into a numbered, step-by-step lesson.\n"
        f"Focus on clarity, predictability, and calm pacing.\n\n"
        f"CONTENT:\n{original}"
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.4,
        )
        text = completion.choices[0].message.content.strip()
        return text
    except Exception as e:
        st.error(f"AI text generation error: {e}")
        # Fallback: simple local step-split
        return "\n\n".join(
            [f"Step {i+1}: {s}" for i, s in enumerate(split_into_steps(original, 2))]
        )

def generate_lesson_image_b64(title: str, original: str) -> str:
    """
    Use OpenAI image model to generate a simple illustration for the lesson.
    Returns base64 string for storage.
    """
    prompt = (
        "Create a simple, friendly, colorful illustration for a children's lesson titled "
        f"'{title}'. The scene should be calm and easy to understand. "
        "Avoid text in the image. The style should be soft and inviting."
    )
    try:
        img = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            n=1
        )
        b64_data = img.data[0].b64_json
        return b64_data
    except Exception as e:
        st.error(f"AI image generation error: {e}")
        return ""

def display_b64_image(b64_data: str, caption: str = ""):
    if not b64_data:
        return
    try:
        img_bytes = base64.b64decode(b64_data)
        st.image(img_bytes, caption=caption, use_column_width=True)
    except Exception as e:
        st.error(f"Error displaying image: {e}")

# -------------------- DB QUERIES --------------------
def get_user_by_email(email: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchone()

def get_user_by_id(uid: int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
    return cur.fetchone()

def search_learners(query: str):
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
    SELECT * FROM users
    WHERE role = 'learner' AND (name LIKE ? OR email LIKE ?)
    ORDER BY name
    """, (like, like))
    return cur.fetchall()

def get_teacher_learners(teacher_id: int):
    cur = conn.cursor()
    cur.execute("""
    SELECT u.* FROM users u
    JOIN teacher_learners tl ON u.id = tl.learner_id
    WHERE tl.teacher_id = ?
    ORDER BY u.name
    """, (teacher_id,))
    return cur.fetchall()

def get_parent_children(parent_id: int):
    cur = conn.cursor()
    cur.execute("""
    SELECT u.* FROM users u
    JOIN parent_children pc ON u.id = pc.learner_id
    WHERE pc.parent_id = ?
    ORDER BY u.name
    """, (parent_id,))
    return cur.fetchall()

def get_assigned_lessons(learner_id: int):
    cur = conn.cursor()
    cur.execute("""
    SELECT la.*, l.title, l.friendly_text, l.original_text, l.image_b64,
           u.name as owner_name, l.owner_role
    FROM lesson_assignments la
    JOIN lessons l ON la.lesson_id = l.id
    JOIN users u ON l.owner_id = u.id
    WHERE la.learner_id = ?
    ORDER BY la.id DESC
    """, (learner_id,))
    return cur.fetchall()

def get_linked_grownups_for_learner(learner_id: int):
    cur = conn.cursor()
    cur.execute("""
    SELECT DISTINCT u.* FROM users u
    LEFT JOIN teacher_learners tl ON (u.id = tl.teacher_id AND tl.learner_id = ?)
    LEFT JOIN parent_children pc ON (u.id = pc.parent_id AND pc.learner_id = ?)
    WHERE (tl.learner_id IS NOT NULL OR pc.learner_id IS NOT NULL)
    """, (learner_id, learner_id))
    return cur.fetchall()

# -------------------- AUTH UI --------------------
def signup_form():
    st.subheader("Create an account")

    role = st.radio(
        "I am signing up as:",
        ["learner", "teacher", "parent"],
        horizontal=True
    )

    if role == "learner":
        st.markdown(
            """
            ### 🌈 Welcome!
            This space is for calm, step-by-step learning.
            You can fill this in with a grown-up if you like.
            """
        )
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Your first name")
        with col2:
            feeling = st.slider("How ready do you feel?", 1, 5, 3)
            st.caption("1 = a bit tired, 5 = very ready!")

        email = st.text_input("Email (yours or a grown-up's)")
        password = st.text_input("Choose a password", type="password")
        password2 = st.text_input("Repeat password", type="password")

    else:
        if role == "teacher":
            st.markdown("### 🧑‍🏫 Teacher sign-up")
        else:
            st.markdown("### 👨‍👩‍👧 Parent sign-up")

        name = st.text_input("Full name")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        password2 = st.text_input("Repeat password", type="password")

    if st.button("Sign up", use_container_width=True):
        if not name or not email or not password:
            st.error("Please fill in all required fields.")
            return

        if password != password2:
            st.error("Passwords do not match.")
            return

        if get_user_by_email(email) is not None:
            st.error("An account with this email already exists.")
            return

        cur = conn.cursor()
        cur.execute("""
        INSERT INTO users (name, email, password_hash, role)
        VALUES (?, ?, ?, ?)
        """, (name, email, hash_password(password), role))
        conn.commit()
        st.success("Account created! You can log in now.")

def login_form():
    st.subheader("Log in")

    email = st.text_input("Email", key="login_email")
    password = st.text_input("Password", type="password", key="login_pw")

    if st.button("Log in", use_container_width=True):
        user = get_user_by_email(email)
        if user is None:
            st.error("No account found with that email.")
            return

        if not check_password(password, user["password_hash"]):
            st.error("Incorrect password.")
            return

        st.session_state.user_id = user["id"]
        st.session_state.user_role = user["role"]
        st.session_state.user_name = user["name"]
        st.rerun()

# -------------------- TEACHER DASHBOARD --------------------
def teacher_dashboard(user):
    st.title(f"👩‍🏫 Welcome, {user['name']}")

    tab_learners, tab_lessons, tab_progress, tab_help = st.tabs(
        ["My learners", "Create lesson", "Lesson progress", "Help requests"]
    )

    # ---- My learners ----
    with tab_learners:
        st.subheader("Find and add learners")

        query = st.text_input("Search learners by name or email")
        if query:
            results = search_learners(query)
            if not results:
                st.info("No learners found.")
            else:
                for r in results:
                    col1, col2, col3 = st.columns([3, 3, 1])
                    with col1:
                        st.markdown(f"**{r['name']}**")
                    with col2:
                        st.caption(r["email"])
                    with col3:
                        if st.button("Add", key=f"add_learner_{r['id']}"):
                            cur = conn.cursor()
                            cur.execute("""
                            INSERT INTO teacher_learners (teacher_id, learner_id)
                            VALUES (?, ?)
                            """, (user["id"], r["id"]))
                            conn.commit()
                            st.success(f"Added {r['name']} as your learner.")

        st.markdown("---")
        st.subheader("Your current learners")
        my_learners = get_teacher_learners(user["id"])
        if not my_learners:
            st.info("No learners added yet.")
        else:
            for l in my_learners:
                st.markdown(f"- {l['name']} ({l['email']})")

    # ---- Create lesson ----
    with tab_lessons:
        st.subheader("Create a calm, step-by-step lesson")

        colA, colB = st.columns([2, 1])
        with colA:
            title = st.text_input("Lesson title")
            mode = st.selectbox("Lesson type", ["chapter", "story"])
            original_text = st.text_area(
                "Paste your chapter or story",
                height=220,
            )

            use_ai = st.checkbox("Use AI to create a gentle version and illustration", value=True)
            generate = st.button("Generate with AI")

        with colB:
            learners = get_teacher_learners(user["id"])
            learner_map = {f"{l['name']} ({l['email']})": l["id"] for l in learners}
            selected_names = st.multiselect(
                "Assign to learners",
                options=list(learner_map.keys()),
            )

        friendly_text = st.session_state.get("friendly_text_teacher", "")
        image_b64 = st.session_state.get("image_b64_teacher", "")

        if generate and original_text:
            if use_ai:
                with st.spinner("Asking AI to create a gentle version..."):
                    friendly_text = generate_friendly_text_with_openai(original_text, mode=mode)
                with st.spinner("Asking AI to draw an illustration..."):
                    image_b64 = generate_lesson_image_b64(title or "Lesson", original_text)
            else:
                friendly_text = "\n\n".join(
                    [f"Step {i+1}: {s}" for i, s in enumerate(split_into_steps(original_text, 2))]
                )
                image_b64 = ""

            st.session_state.friendly_text_teacher = friendly_text
            st.session_state.image_b64_teacher = image_b64

        st.markdown("### Friendly lesson preview")
        friendly_text = st.text_area(
            "You can edit this before sending:",
            value=friendly_text,
            height=260,
        )

        if image_b64:
            st.markdown("### Illustration preview")
            display_b64_image(image_b64, caption="AI-generated illustration")

        if st.button("Save & assign lesson"):
            if not title or not original_text or not friendly_text:
                st.error("Please provide title, text, and generated lesson.")
            else:
                cur = conn.cursor()
                cur.execute("""
                INSERT INTO lessons (owner_id, owner_role, title, original_text, friendly_text, image_b64, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    user["id"], "teacher", title, original_text,
                    friendly_text, image_b64,
                    datetime.datetime.utcnow().isoformat()
                ))
                lesson_id = cur.lastrowid

                for name in selected_names:
                    learner_id = learner_map[name]
                    cur.execute("""
                    INSERT INTO lesson_assignments (lesson_id, learner_id, status, progress_step, completed_at)
                    VALUES (?, ?, 'assigned', 0, NULL)
                    """, (lesson_id, learner_id))
                conn.commit()
                st.success("Lesson created and assigned!")

    # ---- Lesson progress ----
    with tab_progress:
        st.subheader("Learner progress")

        my_learners = get_teacher_learners(user["id"])
        if not my_learners:
            st.info("No learners yet.")
        else:
            learner_ids = tuple(l["id"] for l in my_learners)
            placeholder = ",".join("?" * len(learner_ids))
            cur = conn.cursor()
            cur.execute(f"""
            SELECT la.*, l.title, u.name as learner_name
            FROM lesson_assignments la
            JOIN lessons l ON la.lesson_id = l.id
            JOIN users u ON la.learner_id = u.id
            WHERE la.learner_id IN ({placeholder})
            ORDER BY la.id DESC
            """, learner_ids)
            rows = cur.fetchall()
            if not rows:
                st.info("No lessons assigned yet.")
            else:
                for r in rows:
                    st.markdown(
                        f"- **{r['learner_name']}** – {r['title']} – "
                        f"Status: `{r['status']}` – Step: {r['progress_step']}"
                    )

    # ---- Help requests ----
    with tab_help:
        st.subheader("Help requests from learners")

        cur = conn.cursor()
        cur.execute("""
        SELECT hr.*, u.name as learner_name
        FROM help_requests hr
        JOIN users u ON hr.learner_id = u.id
        WHERE hr.to_user_id = ?
        ORDER BY hr.created_at DESC
        """, (user["id"],))
        rows = cur.fetchall()

        if not rows:
            st.info("No help requests right now.")
        else:
            for r in rows:
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(
                        f"**{r['learner_name']}**: {r['message']}\n\n"
                        f"<small>{r['created_at']}</small>",
                        unsafe_allow_html=True,
                    )
                with col2:
                    if not r["resolved"]:
                        if st.button("Mark resolved", key=f"resolve_{r['id']}"):
                            cur2 = conn.cursor()
                            cur2.execute(
                                "UPDATE help_requests SET resolved = 1 WHERE id = ?",
                                (r["id"],)
                            )
                            conn.commit()
                            st.rerun()
                    else:
                        st.success("Resolved")

# -------------------- PARENT DASHBOARD --------------------
def parent_dashboard(user):
    st.title(f"👨‍👩‍👧 Welcome, {user['name']}")

    tab_kids, tab_lessons, tab_progress, tab_help = st.tabs(
        ["My kids", "Create lesson", "Lesson progress", "Help requests"]
    )

    # ---- My kids ----
    with tab_kids:
        st.subheader("Link to child accounts")

        query = st.text_input("Search learners by name or email", key="kid_search")
        if query:
            results = search_learners(query)
            if not results:
                st.info("No learners found.")
            else:
                for r in results:
                    col1, col2, col3 = st.columns([3, 3, 1])
                    with col1:
                        st.markdown(f"**{r['name']}**")
                    with col2:
                        st.caption(r["email"])
                    with col3:
                        if st.button("Add as my child", key=f"add_child_{r['id']}"):
                            cur = conn.cursor()
                            cur.execute("""
                            INSERT INTO parent_children (parent_id, learner_id)
                            VALUES (?, ?)
                            """, (user["id"], r["id"]))
                            conn.commit()
                            st.success(f"Linked {r['name']} as your child.")

        st.markdown("---")
        st.subheader("Children linked to your account")

        kids = get_parent_children(user["id"])
        if not kids:
            st.info("No child accounts linked yet.")
        else:
            for k in kids:
                st.markdown(f"- {k['name']} ({k['email']})")

    # ---- Create lesson ----
    with tab_lessons:
        st.subheader("Create a gentle lesson for your child")

        kids = get_parent_children(user["id"])
        kid_map = {f"{k['name']} ({k['email']})": k["id"] for k in kids}

        colA, colB = st.columns([2, 1])
        with colA:
            title = st.text_input("Lesson title", key="parent_title")
            mode = st.selectbox("Lesson type", ["chapter", "story"], key="parent_mode")
            original_text = st.text_area(
                "Paste your chapter or story",
                height=220,
                key="parent_original",
            )

            use_ai = st.checkbox("Use AI to create a gentle version and illustration", value=True, key="parent_ai")
            generate = st.button("Generate with AI", key="parent_generate")

        with colB:
            selected_names = st.multiselect(
                "Send to:",
                options=list(kid_map.keys()),
                key="parent_assign_to",
            )

        friendly_text = st.session_state.get("parent_friendly_text", "")
        image_b64 = st.session_state.get("parent_image_b64", "")

        if generate and original_text:
            if use_ai:
                with st.spinner("Asking AI to create a gentle version..."):
                    friendly_text = generate_friendly_text_with_openai(original_text, mode=mode)
                with st.spinner("Asking AI to draw an illustration..."):
                    image_b64 = generate_lesson_image_b64(title or "Lesson", original_text)
            else:
                friendly_text = "\n\n".join(
                    [f"Step {i+1}: {s}" for i, s in enumerate(split_into_steps(original_text, 2))]
                )
                image_b64 = ""

            st.session_state.parent_friendly_text = friendly_text
            st.session_state.parent_image_b64 = image_b64

        st.markdown("### Friendly lesson preview")
        friendly_text = st.text_area(
            "You can edit this before sending:",
            value=friendly_text,
            height=260,
            key="parent_friendly_box",
        )

        if image_b64:
            st.markdown("### Illustration preview")
            display_b64_image(image_b64, caption="AI-generated illustration")

        if st.button("Save & send"):
            if not title or not original_text or not friendly_text:
                st.error("Please fill all fields.")
            else:
                cur = conn.cursor()
                cur.execute("""
                INSERT INTO lessons (owner_id, owner_role, title, original_text, friendly_text, image_b64, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    user["id"], "parent", title, original_text,
                    friendly_text, image_b64,
                    datetime.datetime.utcnow().isoformat()
                ))
                lesson_id = cur.lastrowid

                for name in selected_names:
                    learner_id = kid_map[name]
                    cur.execute("""
                    INSERT INTO lesson_assignments (lesson_id, learner_id, status, progress_step, completed_at)
                    VALUES (?, ?, 'assigned', 0, NULL)
                    """, (lesson_id, learner_id))
                conn.commit()
                st.success("Lesson created and sent!")

    # ---- Lesson progress ----
    with tab_progress:
        st.subheader("Lesson progress for your kids")

        kids = get_parent_children(user["id"])
        if not kids:
            st.info("No children linked yet.")
        else:
            learner_ids = tuple(k["id"] for k in kids)
            placeholder = ",".join("?" * len(learner_ids))
            cur = conn.cursor()
            cur.execute(f"""
            SELECT la.*, l.title, u.name as learner_name
            FROM lesson_assignments la
            JOIN lessons l ON la.lesson_id = l.id
            JOIN users u ON la.learner_id = u.id
            WHERE la.learner_id IN ({placeholder})
            ORDER BY la.id DESC
            """, learner_ids)
            rows = cur.fetchall()
            if not rows:
                st.info("No lessons yet.")
            else:
                for r in rows:
                    st.markdown(
                        f"- **{r['learner_name']}** – {r['title']} – "
                        f"Status: `{r['status']}` – Step: {r['progress_step']}"
                    )

    # ---- Help requests ----
    with tab_help:
        st.subheader("Help requests from your children")

        cur = conn.cursor()
        cur.execute("""
        SELECT hr.*, u.name as learner_name
        FROM help_requests hr
        JOIN users u ON hr.learner_id = u.id
        WHERE hr.to_user_id = ?
        ORDER BY hr.created_at DESC
        """, (user["id"],))
        rows = cur.fetchall()

        if not rows:
            st.info("No help requests at the moment.")
        else:
            for r in rows:
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(
                        f"**{r['learner_name']}**: {r['message']}\n\n"
                        f"<small>{r['created_at']}</small>",
                        unsafe_allow_html=True,
                    )
                with col2:
                    if not r["resolved"]:
                        if st.button("Mark resolved", key=f"parent_resolve_{r['id']}"):
                            cur2 = conn.cursor()
                            cur2.execute(
                                "UPDATE help_requests SET resolved = 1 WHERE id = ?",
                                (r["id"],)
                            )
                            conn.commit()
                            st.rerun()
                    else:
                        st.success("Resolved")

# -------------------- LEARNER DASHBOARD --------------------
def learner_dashboard(user):
    st.title(f"🌈 Hi {user['name']}!")

    st.markdown(
        """
        This is your calm learning space.  
        Tap on a lesson to read it slowly, one step at a time.
        """
    )

    tabs = st.tabs(["My lessons", "Ask for help"])

    # ---- My lessons ----
    with tabs[0]:
        assignments = get_assigned_lessons(user["id"])
        if not assignments:
            st.info("No lessons yet. A teacher or parent can send one to you.")
        else:
            for a in assignments:
                with st.expander(f"{a['title']} – from {a['owner_name']} (status: {a['status']})"):
                    steps = [s for s in a["friendly_text"].split("\n") if s.strip()]
                    total_steps = len(steps)
                    current_step = a["progress_step"]
                    if current_step < 0:
                        current_step = 0
                    if current_step >= total_steps:
                        current_step = total_steps - 1

                    if a["image_b64"]:
                        display_b64_image(a["image_b64"], caption="Lesson picture")

                    st.progress((current_step + 1) / max(1, total_steps))

                    st.markdown(
                        f"<div style='font-size:22px; padding:1rem; border-radius:10px; "
                        f"border:2px solid #ddd;'>{steps[current_step]}</div>",
                        unsafe_allow_html=True,
                    )

                    col_prev, col_next = st.columns(2)
                    with col_prev:
                        if st.button("⬅️ Back", key=f"back_{a['id']}") and current_step > 0:
                            new_step = current_step - 1
                            cur = conn.cursor()
                            cur.execute("""
                            UPDATE lesson_assignments
                            SET progress_step = ?
                            WHERE id = ?
                            """, (new_step, a["id"]))
                            conn.commit()
                            st.rerun()
                    with col_next:
                        if st.button("Next ➡️", key=f"next_{a['id']}") and current_step < total_steps - 1:
                            new_step = current_step + 1
                            cur = conn.cursor()
                            cur.execute("""
                            UPDATE lesson_assignments
                            SET progress_step = ?
                            WHERE id = ?
                            """, (new_step, a["id"]))
                            conn.commit()
                            st.rerun()

                    st.markdown("---")
                    if a["status"] != "completed":
                        if st.button("✅ I finished this lesson", key=f"finish_{a['id']}"):
                            cur = conn.cursor()
                            cur.execute("""
                            UPDATE lesson_assignments
                            SET status = 'completed',
                                progress_step = ?,
                                completed_at = ?
                            WHERE id = ?
                            """, (total_steps - 1, datetime.datetime.utcnow().isoformat(), a["id"]))
                            conn.commit()
                            st.success("Great job! Lesson marked as complete.")
                            st.rerun()
                    else:
                        st.success("Already marked as complete. Well done!")

    # ---- Ask for help ----
    with tabs[1]:
        st.subheader("Ask a grown-up for help")

        grownups = get_linked_grownups_for_learner(user["id"])
        if not grownups:
            st.info("When a teacher or parent links their account to yours, "
                    "you'll be able to send them a message here.")
        else:
            options = {f"{g['name']} ({g['role']})": g["id"] for g in grownups}
            to_label = st.selectbox("Who do you want to ask?", list(options.keys()))
            message = st.text_area("Write your message here:")

            if st.button("Send help request"):
                if not message.strip():
                    st.error("Please write something in your message.")
                else:
                    to_user_id = options[to_label]
                    cur = conn.cursor()
                    cur.execute("""
                    INSERT INTO help_requests (learner_id, to_user_id, message, created_at, resolved)
                    VALUES (?, ?, ?, ?, 0)
                    """, (
                        user["id"], to_user_id, message.strip(),
                        datetime.datetime.utcnow().isoformat()
                    ))
                    conn.commit()
                    st.success("Your message has been sent.")

# -------------------- MAIN APP --------------------
def main():
    st.sidebar.title("Calm Learning Hub")

    if "user_id" not in st.session_state:
        st.session_state.user_id = None
        st.session_state.user_role = None
        st.session_state.user_name = None

    if st.session_state.user_id is None:
        choice = st.sidebar.radio("Welcome", ["Log in", "Sign up"])
        if choice == "Log in":
            login_form()
        else:
            signup_form()
    else:
        user = get_user_by_id(st.session_state.user_id)
        if user is None:
            st.session_state.user_id = None
            st.rerun()

        st.sidebar.markdown(f"**Logged in as:** {user['name']} ({user['role']})")
        if st.sidebar.button("Log out"):
            st.session_state.user_id = None
            st.session_state.user_role = None
            st.session_state.user_name = None
            st.rerun()

        if user["role"] == "teacher":
            teacher_dashboard(user)
        elif user["role"] == "parent":
            parent_dashboard(user)
        else:
            learner_dashboard(user)

if __name__ == "__main__":
    main()