from flask import Flask, render_template, request, redirect, session
from flask_socketio import SocketIO, join_room
import sqlite3
import os
import secrets

from werkzeug.security import generate_password_hash, check_password_hash

from datetime import datetime
from zoneinfo import ZoneInfo

# ---------- APP ----------
app = Flask(__name__)
app.secret_key = "secret123"

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# ---------- FOLDERS ----------
IMAGE_FOLDER = "static/chat_images"
VOICE_FOLDER = "static/voice_notes"
PROFILE_FOLDER = "static/profile_pics"

os.makedirs(IMAGE_FOLDER, exist_ok=True)
os.makedirs(VOICE_FOLDER, exist_ok=True)
os.makedirs(PROFILE_FOLDER, exist_ok=True)

# ---------- ONLINE ----------
online_users = set()
last_seen = {}

# ---------- DATABASE ----------
def get_db():

    conn = sqlite3.connect(
        "database.db",
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn

# ---------- INIT DB ----------
def init_db():

    conn = get_db()
    c = conn.cursor()

    # USERS
    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        username TEXT UNIQUE,
        password TEXT,
        profile_pic TEXT
    )
    """)

    # MESSAGES
    c.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        receiver TEXT,
        message TEXT,
        status TEXT,
        time TEXT,
        type TEXT,
        reply_text TEXT
    )
    """)

    # KEYS
    c.execute("""
    CREATE TABLE IF NOT EXISTS chat_keys(
        user1 TEXT,
        user2 TEXT,
        chat_key TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# ---------- HOME ----------
@app.route('/')
def home():
    return render_template("login.html")

# ---------- REGISTER ----------
@app.route('/register', methods=['GET', 'POST'])
def register():

    if request.method == 'POST':

        user = request.form['username']

        pwd = generate_password_hash(
            request.form['password']
        )

        pic = request.files.get('profile_pic')

        filename = ""

        if pic and pic.filename != "":

            filename = (
                secrets.token_hex(8)
                + "_"
                + os.path.basename(pic.filename)
            )

            pic.save(
                os.path.join(PROFILE_FOLDER, filename)
            )

        conn = get_db()
        c = conn.cursor()

        try:

            c.execute(
                "INSERT INTO users VALUES (?,?,?)",
                (user, pwd, filename)
            )

            conn.commit()

        except:

            conn.close()
            return "User already exists"

        conn.close()

        return redirect('/')

    return render_template("register.html")

# ---------- LOGIN ----------
@app.route('/login', methods=['POST'])
def login():

    user = request.form['username']
    pwd = request.form['password']

    conn = get_db()
    c = conn.cursor()

    c.execute(
        "SELECT * FROM users WHERE username=?",
        (user,)
    )

    data = c.fetchone()

    conn.close()

    if data and check_password_hash(data['password'], pwd):

        session['user'] = user

        return redirect('/chat')

    return "Wrong credentials"

# ---------- CHAT ----------
@app.route('/chat')
def chat():

    if 'user' not in session:
        return redirect('/')

    conn = get_db()
    c = conn.cursor()

    c.execute("""
    SELECT username, profile_pic
    FROM users
    """)

    users = c.fetchall()

    c.execute("""
    SELECT profile_pic
    FROM users
    WHERE username=?
    """,(session['user'],))

    mypic_data = c.fetchone()

    mypic = ""

    if mypic_data:
        mypic = mypic_data['profile_pic']

    conn.close()

    return render_template(
        "chat.html",
        user=session['user'],
        users=users,
        mypic=mypic
    )

# ---------- LOGOUT ----------
@app.route('/logout')
def logout():

    user = session.get('user')

    if user:

        if user in online_users:
            online_users.remove(user)

        last_seen[user] = datetime.now(
            ZoneInfo("Asia/Kolkata")
        ).strftime(
            "%d %b %I:%M %p"
        )

    session.clear()

    socketio.emit("status", {
        "online": list(online_users),
        "last_seen": last_seen
    })

    return redirect('/')

# ---------- GET KEY ----------
@app.route('/get_key/<user>')
def get_key(user):

    current = session['user']

    conn = get_db()
    c = conn.cursor()

    c.execute("""
    SELECT chat_key
    FROM chat_keys
    WHERE (user1=? AND user2=?)
    OR (user1=? AND user2=?)
    """, (
        current,
        user,
        user,
        current
    ))

    row = c.fetchone()

    if row:

        key = row['chat_key']

    else:

        key = secrets.token_hex(16)

        c.execute("""
        INSERT INTO chat_keys
        VALUES (?,?,?)
        """, (
            current,
            user,
            key
        ))

        conn.commit()

    conn.close()

    return {"key": key}

# ---------- GET MESSAGES ----------
@app.route('/get_messages/<user>')
def get_messages(user):

    current = session['user']

    conn = get_db()
    c = conn.cursor()

    # AUTO SEEN
    c.execute("""
    UPDATE messages
    SET status='seen'
    WHERE receiver=?
    AND sender=?
    """, (
        current,
        user
    ))

    conn.commit()

    c.execute("""
    SELECT id,
           sender,
           message,
           status,
           time,
           type,
           reply_text
    FROM messages
    WHERE (sender=? AND receiver=?)
    OR (sender=? AND receiver=?)
    ORDER BY id ASC
    """, (
        current,
        user,
        user,
        current
    ))

    data = c.fetchall()

    conn.close()

    return {
        "messages": [
            [
                row["id"],
                row["sender"],
                row["message"],
                row["status"],
                row["time"],
                row["type"],
                row["reply_text"]
            ]
            for row in data
        ]
    }

# ---------- UPLOAD IMAGE ----------
@app.route('/upload_image', methods=['POST'])
def upload_image():

    file = request.files['file']

    filename = (
        secrets.token_hex(8)
        + "_"
        + os.path.basename(file.filename)
    )

    file.save(
        os.path.join(
            IMAGE_FOLDER,
            filename
        )
    )

    return {"filename": filename}

# ---------- UPLOAD VOICE ----------
@app.route('/upload_voice', methods=['POST'])
def upload_voice():

    file = request.files['audio']

    filename = secrets.token_hex(8) + ".webm"

    file.save(
        os.path.join(
            VOICE_FOLDER,
            filename
        )
    )

    return {"filename": filename}

# ---------- DELETE MESSAGE ----------
@app.route('/delete_message/<int:msg_id>', methods=['POST'])
def delete_message(msg_id):

    conn = get_db()
    c = conn.cursor()

    c.execute("""
    DELETE FROM messages
    WHERE id=?
    """, (msg_id,))

    conn.commit()
    conn.close()

    socketio.emit("message")

    return {"status": "deleted"}

# ---------- EDIT MESSAGE ----------
@app.route('/edit_message/<int:msg_id>', methods=['POST'])
def edit_message(msg_id):

    new_text = request.json.get("message")

    conn = get_db()
    c = conn.cursor()

    c.execute("""
    UPDATE messages
    SET message=?
    WHERE id=?
    """, (
        new_text,
        msg_id
    ))

    conn.commit()
    conn.close()

    socketio.emit("message")

    return {"status": "edited"}

# ---------- JOIN ----------
@socketio.on('join')
def join(data):

    user = data['username']

    online_users.add(user)

    join_room(user)

    socketio.emit("status", {
        "online": list(online_users),
        "last_seen": last_seen
    })

# ---------- DISCONNECT ----------
@socketio.on('disconnect')
def disconnect_user():

    user = session.get('user')

    if user:

        if user in online_users:
            online_users.remove(user)

        last_seen[user] = datetime.now(
            ZoneInfo("Asia/Kolkata")
        ).strftime(
            "%d %b %I:%M %p"
        )

    socketio.emit("status", {
        "online": list(online_users),
        "last_seen": last_seen
    })

# ---------- PRIVATE MESSAGE ----------
@socketio.on('private_message')
def private_message(data):

    sender = session['user']

    receiver = data['to']

    msg = data['msg']

    msg_type = data.get('type', 'text')

    reply_text = data.get('reply', '')

    time = datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).strftime(
        "%d %b %I:%M %p"
    )

    conn = get_db()
    c = conn.cursor()

    c.execute("""
    INSERT INTO messages(
        sender,
        receiver,
        message,
        status,
        time,
        type,
        reply_text
    )
    VALUES (?,?,?,?,?,?,?)
    """, (
        sender,
        receiver,
        msg,
        'sent',
        time,
        msg_type,
        reply_text
    ))

    conn.commit()
    conn.close()

    socketio.emit(
        'message',
        room=receiver
    )

    socketio.emit(
        'message',
        room=sender
    )

# ---------- SEEN ----------
@socketio.on('seen')
def seen(data):

    conn = get_db()
    c = conn.cursor()

    c.execute("""
    UPDATE messages
    SET status='seen'
    WHERE id=?
    """, (data['id'],))

    conn.commit()
    conn.close()

    socketio.emit('message')

# ---------- RUN ----------
if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False
    )