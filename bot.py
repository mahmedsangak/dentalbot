import os
import re
import io
import csv
import json
import time
import asyncio
import logging
import traceback
from contextlib import contextmanager
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ------------------ الإعدادات والملفات ------------------
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 5524792549  # المالك الوحيد الذي لا يُحذف ولا تُقيّد صلاحياته

CODES_FILE = "codes.json"
LOGGED_FILE = "logged_users.txt"
ALL_USERS_FILE = "all_users.txt"
BOT_FILES_JSON = "bot_files.json"
STATS_FILE = "stats.json"
ADMINS_FILE = "admins.json"            # يحتوي قائمة الأدمنز (IDs فقط باستثناء المالك)
ADMIN_PERMS_FILE = "admin_perms.json"  # يحتوي صلاحيات كل أدمن
SUSPENDED_FILE = "suspended.json"
COMPLAINTS_FILE = "complaints.json"
USERS_FILE = "users.json"              # سجل المستخدمين (id -> {name, username, code})

# أنواع الملفات وحجمها
ALLOWED_EXTS = {".pdf", ".ppt", ".pptx", ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".avi", ".mkv", ".mov"}
MAX_UPLOAD_SIZE = 3 * 1024 * 1024 * 1024  # 3GB


# ------------------ أزرار عامة ------------------
BACK_BTN = "⬅️ رجوع للخلف"
MAIN_BTN = "🏠 القائمة الرئيسية"
CANCEL_UPLOAD_BTN = "❌ إلغاء الرفع"
CANCEL_ACTION_BTN = "❌ إلغاء الأمر"
CONFIRM_DELETE_BTN = "✅ تأكيد الحذف"
CONFIRM_SEND_BTN = "✅ تأكيد الإرسال"
ADMIN_PANEL_BTN = "🛠️ خصائص الأدمن"
SEND_SUGGEST_BTN = "📝 ارسال مقترح او شكوي لادارة الكلية"
ADMIN_VIEW_COMPLAINTS_BTN = "📬 عرض المقترحات والشكاوي"

# إدارة المحتوى
RENAME_MENU_BTN = "✏️ إعادة تسمية"
DELETE_MENU_BTN = "🗑️ حذف"
RENAME_SUBJECT_BTN = "✏️ إعادة تسمية مادة"
RENAME_LECTURE_BTN = "✏️ إعادة تسمية محاضرة"
RENAME_FILE_BTN = "✏️ إعادة تسمية ملف"
DELETE_SUBJECT_BTN = "🗑️ حذف مادة"
DELETE_LECTURE_BTN = "🗑️ حذف محاضرة"
DELETE_FILE_BTN = "🗑️ حذف ملف"

# إدارة الطلاب
ADMIN_ADD_STUDENT_BTN = "➕ إضافة طالب"
ADMIN_EDIT_STUDENT_BTN = "✏️ تعديل طالب"
ADMIN_DELETE_STUDENT_BTN = "🗑️ حذف طالب"
ADMIN_SUSPEND_STUDENT_BTN = "⏸️ إيقاف/إلغاء إيقاف حساب"

SELECT_BY_CODE_BTN = "🔢 إدخال الكود"
SELECT_FROM_LIST_BTN = "📋 عرض جميع الطلاب"
EDIT_NAME_BTN = "✏️ تعديل الاسم"
EDIT_CODE_BTN = "🔢 تعديل الكود"
UNSUSPEND_BTN = "✅ إلغاء الإيقاف"
SUSPEND_BTN = "⏸️ إيقاف الآن"

# الأكواد والبث والإحصائيات
IMPORT_CODES_BTN = "📥 استيراد أكواد CSV"
BROADCAST_BTN = "📢 بث إشعار"
STATS_BTN = "📊 إحصائيات البوت"

# السوبر أدمن - إدارة الأدمنز
MANAGE_ADMINS_BTN = "👑 إدارة الأدمنز"
LIST_ADMINS_BTN = "📋 عرض الأدمنز"
ADD_ADMIN_BTN = "➕ إضافة أدمن"
EDIT_ADMIN_PERMS_BTN = "✏️ تعديل صلاحيات الأدمن"
DELETE_ADMIN_BTN = "🗑️ حذف أدمن"

ADD_BY_ID_BTN = "🔢 إضافة عبر ID"
ADD_BY_USERNAME_BTN = "🔤 إضافة عبر Username"
ADD_BY_CONTACT_BTN = "📱 إضافة عبر جهة اتصال"

# صفحات
NEXT_BTN = "▶️ التالي"
PREV_BTN = "◀️ السابق"
PAGE_SIZE = 10
STUDENTS_PAGE_SIZE = 10

# مفاتيح الصلاحيات
PERM_KEYS = [
    ("content", "1- اضافة محاضرات"),               # إضافة/حذف/إعادة تسمية مواد/محاضرات/عناصر
    ("student_add_delete", "2- اضافة طالب وحذفه"),
    ("student_edit", "3- تعديل بيانات الطالب"),
    ("suspend", "4- ايقاف او الغاء الحسابات"),
    ("complaints", "5- عرض المقترحات والشكاوي"),
    ("stats", "6- احصائيات البوت"),
    ("broadcast", "7- بث اشعار"),
]

# ------------------ اللوج ------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

# ------------------ أدوات تخزين آمنة ------------------
def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)

@contextmanager
def file_lock(base_path: str, timeout: float = 10.0, poll: float = 0.05):
    lock_path = base_path + ".lock"
    start = time.time()
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            if time.time() - start > timeout:
                raise TimeoutError(f"Timeout acquiring lock for {base_path}")
            time.sleep(poll)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
            if os.path.exists(lock_path):
                os.unlink(lock_path)
        except Exception:
            pass

def append_line_safe(path, line):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "ab+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            last = f.read(1)
            if last not in (b"\n", b"\r"):
                f.write(b"\n")
        f.write((line + "\n").encode("utf-8"))

# ------------------ دوال مساعدة ------------------
def normalize_code(code: str) -> str:
    arabic_to_english = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    normalized = str(code).translate(arabic_to_english)
    normalized = "".join(filter(str.isdigit, normalized))
    return normalized

# --------- سجل مستخدمين عام ----------
def load_users():
    if not os.path.exists(USERS_FILE):
        atomic_write_json(USERS_FILE, {})
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    with file_lock(USERS_FILE):
        atomic_write_json(USERS_FILE, data)

def update_user_registry_from_update(update: Update):
    if not update or not update.effective_user:
        return
    u = update.effective_user
    users = load_users()
    entry = users.get(str(u.id), {})
    entry["name"] = (u.full_name or "").strip()
    entry["username"] = (u.username or "")
    users[str(u.id)] = entry
    save_users(users)

def update_user_code(user_id: int, code: str):
    users = load_users()
    entry = users.get(str(user_id), {})
    entry["code"] = normalize_code(code)
    users[str(user_id)] = entry
    save_users(users)

def find_user_id_by_username(username: str):
    username = username.strip().lstrip("@").lower()
    users = load_users()
    for uid, info in users.items():
        if str(info.get("username", "")).lower() == username:
            return int(uid)
    return None

def admin_label(admin_id: int):
    users = load_users()
    info = users.get(str(admin_id), {})
    name = info.get("name", "") or "-"
    un = info.get("username", "")
    un = f"@{un}" if un else "-"
    code = info.get("code", "")
    code_txt = f" | كود: {code}" if code else ""
    return f"ID:{admin_id} | {name} ({un}){code_txt}"

# --------- إدارة الأدمن + الصلاحيات ----------
_admins_set = set()  # IDs (بدون المالك)
_admin_perms = {}    # str(admin_id) -> dict(perms)

def load_admins():
    global _admins_set
    if not os.path.exists(ADMINS_FILE):
        atomic_write_json(ADMINS_FILE, [])
    try:
        with open(ADMINS_FILE, "r", encoding="utf-8") as f:
            ids = json.load(f)
            _admins_set = set(int(x) for x in ids if str(x).isdigit())
    except Exception:
        _admins_set = set()

def save_admins():
    # لا نخزن المالك في الملف
    to_save = sorted([i for i in _admins_set if i != OWNER_ID])
    with file_lock(ADMINS_FILE):
        atomic_write_json(ADMINS_FILE, to_save)


def is_admin(user_id: int) -> bool:
    return int(user_id) == OWNER_ID or int(user_id) in _admins_set

def is_super_admin(user_id: int) -> bool:
    return int(user_id) == OWNER_ID

def get_admin_ids():
    # جميع الأدمنز: المالك + باقي الأدمنز
    return [OWNER_ID] + sorted([i for i in _admins_set if i != OWNER_ID])

def load_admin_perms():
    global _admin_perms
    if not os.path.exists(ADMIN_PERMS_FILE):
        atomic_write_json(ADMIN_PERMS_FILE, {})
    try:
        with open(ADMIN_PERMS_FILE, "r", encoding="utf-8") as f:
            _admin_perms = json.load(f)
    except Exception:
        _admin_perms = {}

def save_admin_perms(perms=None):
    if perms is not None:
        data = perms
    else:
        data = _admin_perms
    with file_lock(ADMIN_PERMS_FILE):
        atomic_write_json(ADMIN_PERMS_FILE, data)

def ensure_admin_perms_entry(admin_id: int):
    sid = str(admin_id)
    if sid not in _admin_perms:
        # افتراضي: كل الصلاحيات False
        _admin_perms[sid] = {k: False for k, _ in PERM_KEYS}
        save_admin_perms()

def can_admin(user_id: int, perm: str) -> bool:
    # المالك يمتلك كل الصلاحيات
    if is_super_admin(user_id):
        return True
    sid = str(user_id)
    entry = _admin_perms.get(sid, {})
    return bool(entry.get(perm, False))

def get_admins_with_perm(perm: str):
    return [aid for aid in get_admin_ids() if can_admin(aid, perm)]

# --------- الأكواد (الطلاب) ----------
def load_codes():
    if not os.path.exists(CODES_FILE):
        atomic_write_json(CODES_FILE, [])
    with open(CODES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_codes(codes_list):
    with file_lock(CODES_FILE):
        atomic_write_json(CODES_FILE, codes_list)

def get_code_map():
    data = load_codes()
    res = {}
    for it in data:
        c = normalize_code(str(it.get("code", "")))
        n = str(it.get("name", "")).strip()
        if c:
            res[c] = n
    return res

def check_code(student_code):
    user_code = normalize_code(student_code)
    if not user_code:
        return None
    code_map = get_code_map()
    if user_code in code_map:
        return {"code": user_code, "name": code_map[user_code]}
    return None

# --------- إيقاف مؤقت ----------
def load_suspended():
    if not os.path.exists(SUSPENDED_FILE):
        atomic_write_json(SUSPENDED_FILE, {})
    with open(SUSPENDED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_suspended(data):
    with file_lock(SUSPENDED_FILE):
        atomic_write_json(SUSPENDED_FILE, data)

def is_code_suspended(code):
    data = load_suspended()
    c = normalize_code(code)
    if c in data:
        return data[c]  # dict: reason, by, ts
    return None

def suspend_code(code, reason, by_id):
    c = normalize_code(code)
    data = load_suspended()
    data[c] = {"reason": reason, "by": int(by_id), "ts": int(time.time())}
    save_suspended(data)

def unsuspend_code(code):
    c = normalize_code(code)
    data = load_suspended()
    if c in data:
        del data[c]
        save_suspended(data)

# --------- ملفات المحاضرات ----------
def load_bot_files():
    if not os.path.exists(BOT_FILES_JSON):
        atomic_write_json(BOT_FILES_JSON, {})
    with open(BOT_FILES_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def save_bot_files(bot_files):
    with file_lock(BOT_FILES_JSON):
        atomic_write_json(BOT_FILES_JSON, bot_files)

# --------- الشكاوى/المقترحات ----------
def load_complaints():
    if not os.path.exists(COMPLAINTS_FILE):
        atomic_write_json(COMPLAINTS_FILE, [])
    with open(COMPLAINTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_complaints(lst):
    with file_lock(COMPLAINTS_FILE):
        atomic_write_json(COMPLAINTS_FILE, lst)

def append_complaint(user_id, name, username, text):
    lst = load_complaints()
    new_id = (max([c.get("id", 0) for c in lst]) + 1) if lst else 1
    record = {
        "id": new_id,
        "user_id": int(user_id),
        "name": name or "",
        "username": username or "",
        "text": text,
        "ts": int(time.time())
    }
    lst.append(record)
    save_complaints(lst)
    return record

# --------- سجل الدخول (إصلاح اللزق) ----------
def _read_logged_entries():
    if not os.path.exists(LOGGED_FILE):
        return []
    with open(LOGGED_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return re.findall(r"(\d+)\|([^\n\r]*)", content)

def _write_logged_entries(entries):
    dedup = {}
    for uid, name in entries:
        dedup[str(uid)] = name
    with open(LOGGED_FILE, "w", encoding="utf-8") as f:
        for uid, name in dedup.items():
            f.write(f"{uid}|{name}\n")

def fix_logged_file():
    entries = _read_logged_entries()
    _write_logged_entries(entries)

def is_logged_in(user_id):
    uid = str(user_id)
    for u, _ in _read_logged_entries():
        if u == uid:
            return True
    return False

def log_user(user_id, student_name, student_code=None):
    uid = str(user_id)
    entries = _read_logged_entries()
    entries = [(u, n) for (u, n) in entries if u != uid]
    entries.append((uid, student_name))
    _write_logged_entries(entries)
    append_line_safe(ALL_USERS_FILE, uid)
    if student_code:
        update_user_code(user_id, student_code)

def logout_user(user_id):
    uid = str(user_id)
    entries = _read_logged_entries()
    entries = [(u, n) for (u, n) in entries if u != uid]
    _write_logged_entries(entries)

def get_logged_name(user_id):
    uid = str(user_id)
    for u, name in _read_logged_entries():
        if u == uid:
            return name
    return None

# --------- إحصائيات ---------
def load_json_safe(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def load_stats():
    stats = load_json_safe(STATS_FILE, {})
    stats.setdefault("downloads_total", 0)
    stats.setdefault("file_downloads", {})
    stats.setdefault("user_activity", {})
    return stats

def save_stats(stats):
    atomic_write_json(STATS_FILE, stats)

def update_user_activity(user_id):
    stats = load_stats()
    stats["user_activity"][str(user_id)] = int(time.time())
    save_stats(stats)

def inc_download_count(subject, lecture, filename):
    key = f"{subject}|{lecture}|{filename}"
    stats = load_stats()
    stats["downloads_total"] = int(stats.get("downloads_total", 0)) + 1
    file_downloads = stats.get("file_downloads", {})
    file_downloads[key] = int(file_downloads.get(key, 0)) + 1
    stats["file_downloads"] = file_downloads
    save_stats(stats)

def get_stats_summary():
    users = set()
    if os.path.exists(ALL_USERS_FILE):
        with open(ALL_USERS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    users.add(int(line))
    total_users = len(users)
    stats = load_stats()
    now = time.time()
    active_7d = sum(1 for ts in stats["user_activity"].values() if now - int(ts) <= 7*24*3600)
    downloads_total = int(stats.get("downloads_total", 0))
    files_with_downloads = sum(1 for v in stats.get("file_downloads", {}).values() if int(v) > 0)
    top_items = sorted(stats.get("file_downloads", {}).items(), key=lambda x: x[1], reverse=True)[:5]
    top_lines = []
    for k, cnt in top_items:
        parts = k.split("|")
        if len(parts) == 3:
            subj, lect, fname = parts
        else:
            subj, lect, fname = ("?", "?", k)
        top_lines.append(f"- {subj} > {lect} > {fname}: {cnt}")
    return {
        "total_users": total_users,
        "active_7d": active_7d,
        "downloads_total": downloads_total,
        "files_with_downloads": files_with_downloads,
        "top_lines": top_lines
    }

# ------------------ نظام التنقل (Stack) ------------------
def push_state(context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    prev_menu = user_data.get("current_menu")
    if prev_menu is None:
        return
    snapshot = {"menu": prev_menu}
    for key in [
        "selected_subject", "selected_lecture", "selected_file",
        "complaints_page", "students_page", "selected_student_code",
        "admins_page", "selected_admin_id"
    ]:
        if key in user_data:
            snapshot[key] = user_data[key]
    stack = user_data.setdefault("menu_stack", [])
    stack.append(snapshot)

def enter_menu(context: ContextTypes.DEFAULT_TYPE, new_menu: str):
    if not context.user_data.get("_restoring"):
        push_state(context)
    context.user_data["current_menu"] = new_menu

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stack = context.user_data.get("menu_stack", [])
    if not stack:
        await show_main_menu(update, context, user_id)
        return
    state = stack.pop()
    context.user_data["current_menu"] = state.get("menu")
    for key, val in state.items():
        if key != "menu":
            context.user_data[key] = val
    context.user_data["_restoring"] = True
    await render_current_menu(update, context)
    context.user_data.pop("_restoring", None)

def clear_nav_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["menu_stack"] = []
    context.user_data["current_menu"] = "main"

def breadcrumbs(context):
    ss = context.user_data.get("selected_subject")
    sl = context.user_data.get("selected_lecture")
    path = "📂 المحاضرات"
    if ss:
        path += f" > 🧬 {ss}"
    if sl:
        path += f" > 🧾 {sl}"
    return path

async def render_current_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    menu = context.user_data.get("current_menu")
    if menu == "main":
        await show_main_menu(update, context, user_id)
    elif menu == "my_data":
        await show_my_data(update, context)
    elif menu == "view_subjects":
        await show_subjects_menu(update, context)
    elif menu == "view_lectures":
        ss = context.user_data.get("selected_subject")
        if ss:
            await show_lectures_menu(update, context, ss)
        else:
            await show_subjects_menu(update, context)
    elif menu == "view_files":
        ss = context.user_data.get("selected_subject")
        sl = context.user_data.get("selected_lecture")
        if ss and sl:
            await show_files_menu(update, context, ss, sl)
        else:
            await show_subjects_menu(update, context)
    elif menu == "admin_panel":
        await show_admin_panel(update, context)
    elif menu == "admin_complaints_list":
        await show_admin_complaints_list(update, context, context.user_data.get("complaints_page", 0))
    elif menu == "delete_student_list":
        await show_students_list_for_delete(update, context, context.user_data.get("students_page", 0))
    elif menu == "edit_student_list":
        await show_students_list_for_edit(update, context, context.user_data.get("students_page", 0))
    elif menu == "suspend_student_list":
        await show_students_list_for_suspend(update, context, context.user_data.get("students_page", 0))
    elif menu == "manage_admins":
        await show_manage_admins_menu(update, context)
    elif menu == "list_admins":
        await show_admins_list(update, context, context.user_data.get("admins_page", 0))
    elif menu == "edit_admin_perms_select":
        await show_admins_list(update, context, context.user_data.get("admins_page", 0), purpose="edit_perms")
    elif menu == "delete_admin_select":
        await show_admins_list(update, context, context.user_data.get("admins_page", 0), purpose="delete_admin")
    elif menu == "edit_admin_perms":
        await show_edit_admin_perms_menu(update, context, context.user_data.get("selected_admin_id"))
    else:
        await show_main_menu(update, context, user_id)

# ------------------ واجهات العرض الأساسية ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_user_registry_from_update(update)
    if is_logged_in(user_id):
        await show_main_menu(update, context, user_id)
        return
    welcome_msg = (
        "👋 أهلاً بك في بوت الجامعة!\n\n"
        "من فضلك أدخل كود الطالب الخاص بك:\n\n"
        "البوت مطور بواسطة محمد: https://facebook.com/MSANGAK27"
    )
    keyboard = [[KeyboardButton("برجاء إدخال كود الطالب")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup)

async def show_main_menu(update, context, user_id=None):
    keyboard = [
        [KeyboardButton("📚 المحاضرات")],
        [KeyboardButton("👤 بياناتي")],
        [KeyboardButton(SEND_SUGGEST_BTN)],
        [KeyboardButton("🚪 تسجيل الخروج")]
    ]
    if is_admin(user_id):
        keyboard.insert(0, [KeyboardButton(ADMIN_PANEL_BTN)])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("اختر من القائمة:", reply_markup=reply_markup)
    clear_nav_state(context)

async def show_my_data(update, context):
    enter_menu(context, "my_data")
    user_id = update.effective_user.id
    student_name = get_logged_name(user_id) or "غير معروف"
    keyboard = [
        [KeyboardButton(f"📝 الاسم: {student_name}")],
        [KeyboardButton("📆 الجدول الدراسي")],
        [KeyboardButton("🕒 الغياب والحضور")],
        [KeyboardButton(BACK_BTN)],
        [KeyboardButton(MAIN_BTN)]
    ]
    await update.message.reply_text("👤 بياناتي:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_subjects_menu(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد محاضرات حالياً.")
        return
    enter_menu(context, "view_subjects")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text(f"{breadcrumbs(context)}\nاختر المادة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_lectures_menu(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files:
        await update.message.reply_text("❌ هذه المادة غير موجودة!")
        return
    lectures = list(bot_files[selected_subject].keys())
    if not lectures:
        await update.message.reply_text("❌ لا توجد محاضرات في هذه المادة.")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "view_lectures")
    keyboard = [[KeyboardButton(l)] for l in lectures]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text(f"{breadcrumbs(context)}\nاختر المحاضرة في مادة {selected_subject}:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_files_menu(update, context, selected_subject, selected_lecture):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or selected_lecture not in bot_files[selected_subject]:
        await update.message.reply_text("❌ هذه المحاضرة غير موجودة!")
        return
    files = bot_files[selected_subject][selected_lecture]
    if not files:
        await update.message.reply_text("❌ لا توجد ملفات في هذه المحاضرة.")
        return
    context.user_data["selected_subject"] = selected_subject
    context.user_data["selected_lecture"] = selected_lecture
    enter_menu(context, "view_files")
    keyboard = [[KeyboardButton(f)] for f in files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text(f"{breadcrumbs(context)}\nاختر الملف الذي تريد تحميله في {selected_lecture}:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

# ------------------ لوحة الأدمن (ديناميكية حسب الصلاحيات) ------------------
def build_admin_panel_keyboard(user_id: int):
    rows = []
    # إدارة الأدمنز للسوبر فقط
    if is_super_admin(user_id):
        rows.append([KeyboardButton(MANAGE_ADMINS_BTN)])
    # إدارة المحتوى
    if can_admin(user_id, "content"):
        rows.append([KeyboardButton("➕ إضافة مادة جديدة"), KeyboardButton("➕ إضافة محاضرة جديدة")])
        rows.append([KeyboardButton("➕ إضافة عنصر جديد")])
        rows.append([KeyboardButton(RENAME_MENU_BTN), KeyboardButton(DELETE_MENU_BTN)])
    # إدارة الطلاب
    if can_admin(user_id, "student_add_delete") or can_admin(user_id, "student_edit") or can_admin(user_id, "suspend"):
        subrow = []
        if can_admin(user_id, "student_add_delete"):
            subrow.append(KeyboardButton(ADMIN_ADD_STUDENT_BTN))
        if can_admin(user_id, "student_edit"):
            subrow.append(KeyboardButton(ADMIN_EDIT_STUDENT_BTN))
        if subrow:
            rows.append(subrow)
        if can_admin(user_id, "student_add_delete"):
            rows.append([KeyboardButton(ADMIN_DELETE_STUDENT_BTN)])
        if can_admin(user_id, "suspend"):
            rows.append([KeyboardButton(ADMIN_SUSPEND_STUDENT_BTN)])
    # استيراد الأكواد
    if can_admin(user_id, "student_add_delete"):
        rows.append([KeyboardButton(IMPORT_CODES_BTN)])
    # الشكاوى
    if can_admin(user_id, "complaints"):
        rows.append([KeyboardButton(ADMIN_VIEW_COMPLAINTS_BTN)])
    # بث وإحصائيات
    last = []
    if can_admin(user_id, "broadcast"):
        last.append(KeyboardButton(BROADCAST_BTN))
    if can_admin(user_id, "stats"):
        last.append(KeyboardButton(STATS_BTN))
    if last:
        rows.append(last)
    rows.append([KeyboardButton(BACK_BTN)])
    rows.append([KeyboardButton(MAIN_BTN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def show_admin_panel(update, context):
    enter_menu(context, "admin_panel")
    uid = update.effective_user.id
    await update.message.reply_text("🛠️ خصائص الأدمن:", reply_markup=build_admin_panel_keyboard(uid))

# ------------------ إدارة الشكاوى/المقترحات ------------------
async def user_suggest_start(update, context):
    enter_menu(context, "user_suggest_text")
    keyboard = [[KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("✍️ اكتب رسالتك (مقترح/شكوى) ثم أرسلها:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def user_suggest_confirm(update, context, text):
    context.user_data["pending_complaint_text"] = text
    enter_menu(context, "user_suggest_confirm")
    keyboard = [[KeyboardButton(CONFIRM_SEND_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text(f"📨 رسالتك:\n{text}\n\nهل ترغب في إرسالها للإدارة؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def user_suggest_send(update, context):
    text = context.user_data.get("pending_complaint_text", "").strip()
    if not text:
        await update.message.reply_text("❌ لا توجد رسالة لإرسالها.")
        return
    user = update.effective_user
    name = (user.full_name or "").strip()
    username = (user.username or "")
    rec = append_complaint(user.id, name, username, text)
    # إخطار الأدمنز أصحاب صلاحية الشكاوى فقط
    note = f"📬 شكوى/مقترح جديد #{rec['id']}:\n- من: {name} (@{username}) | ID: {user.id}\n- الوقت: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rec['ts']))}\n\nالمحتوى:\n{text}"
    receivers = get_admins_with_perm("complaints")
    for aid in receivers:
        try:
            await context.bot.send_message(aid, note)
        except Exception:
            pass
    await update.message.reply_text("✅ تم إرسال رسالتك إلى إدارة الكلية. شكرًا لمشاركتك.")
    await show_main_menu(update, context, user.id)

async def show_admin_complaints_list(update, context, page=0):
    enter_menu(context, "admin_complaints_list")
    lst = load_complaints()
    total = len(lst)
    if total == 0:
        await update.message.reply_text("لا توجد شكاوى/مقترحات حتى الآن.")
        return
    lst_sorted = sorted(lst, key=lambda x: x.get("id", 0), reverse=True)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = lst_sorted[start:end]
    keyboard = [[KeyboardButton(f"شكوى/اقتراح #{item['id']}")] for item in page_items]
    nav = []
    if page > 0:
        nav.append(KeyboardButton(PREV_BTN))
    if end < total:
        nav.append(KeyboardButton(NEXT_BTN))
    if nav:
        keyboard.append(nav)
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    context.user_data["complaints_page"] = page
    await update.message.reply_text(f"📬 الشكاوى/المقترحات (الصفحة {page+1} من {((total-1)//PAGE_SIZE)+1}):", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_admin_complaint_detail(update, context, comp_id):
    lst = load_complaints()
    comp = next((c for c in lst if int(c.get("id")) == int(comp_id)), None)
    if not comp:
        await update.message.reply_text("❌ لم يتم العثور على هذه الشكوى/الاقتراح.")
        return
    txt = comp.get("text", "")
    name = comp.get("name", "")
    username = comp.get("username", "")
    ts = comp.get("ts", 0)
    info = f"📄 شكوى/اقتراح #{comp_id}\n- المرسل: {name} (@{username})\n- الوقت: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}\n\nالنص:\n{txt}"
    await update.message.reply_text(info, reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))

# ------------------ إدارة المحتوى (مواد/محاضرات/ملفات) ------------------
async def show_add_subject_prompt(update, context):
    enter_menu(context, "add_subject")
    keyboard = [[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("✏️ أدخل اسم المادة الجديدة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_add_lecture_select_subject(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد حالياً. أضف مادة أولاً.")
        return
    enter_menu(context, "add_lecture_subject")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("📌 اختر المادة لإضافة المحاضرة إليها:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_add_lecture_prompt_name(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files:
        await update.message.reply_text("❌ هذه المادة غير موجودة!")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "add_lecture_name")
    existing = list(bot_files[selected_subject].keys())
    if existing:
        await update.message.reply_text("المحاضرات الحالية: " + ", ".join(existing))
    keyboard = [[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("✏️ أدخل اسم المحاضرة الجديدة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_add_item_select_subject(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد حالياً. أضف مادة أولاً.")
        return
    enter_menu(context, "add_item_subject")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("📌 اختر المادة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_add_item_select_lecture(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files:
        await update.message.reply_text("❌ هذه المادة غير موجودة!")
        return
    lectures = list(bot_files[selected_subject].keys())
    if not lectures:
        await update.message.reply_text("❌ لا توجد محاضرات في هذه المادة. أضف محاضرة أولاً.")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "add_item_lecture")
    keyboard = [[KeyboardButton(l)] for l in lectures]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("📌 اختر المحاضرة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_add_item_prompt_file(update, context, selected_subject, selected_lecture):
    context.user_data["selected_subject"] = selected_subject
    context.user_data["selected_lecture"] = selected_lecture
    enter_menu(context, "add_item_file")
    keyboard = [[KeyboardButton(CANCEL_UPLOAD_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("📎 أرسل الملف الذي تريد رفعه:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

def is_allowed_upload(file_obj):
    name = getattr(file_obj, "file_name", "") or ""
    size = getattr(file_obj, "file_size", 0) or 0
    ext = os.path.splitext(name)[1].lower() if name else ""
    if name and ext not in ALLOWED_EXTS:
        return False
    if size and size > MAX_UPLOAD_SIZE:
        return False
    return True

# ------------------ إعادة التسمية ------------------
async def rename_subject_start(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد.")
        return
    enter_menu(context, "rename_subject_select")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("✏️ اختر المادة التي تريد إعادة تسميتها:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def rename_subject_newname(update, context, selected_subject, new_name):
    bot_files = load_bot_files()
    if selected_subject not in bot_files:
        await update.message.reply_text("❌ المادة غير موجودة.")
        return
    new_name = new_name.strip()
    if not new_name:
        await update.message.reply_text("❌ اسم غير صالح.")
        return
    if new_name in bot_files:
        await update.message.reply_text("❌ يوجد مادة بنفس الاسم.")
        return
    bot_files[new_name] = bot_files.pop(selected_subject)
    save_bot_files(bot_files)
    await update.message.reply_text(f"✅ تم تغيير اسم المادة إلى: {new_name}")
    await show_admin_panel(update, context)

async def rename_lecture_start(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد.")
        return
    enter_menu(context, "rename_lecture_select_subject")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("✏️ اختر المادة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def rename_lecture_select_lecture(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or not bot_files[selected_subject]:
        await update.message.reply_text("❌ المادة غير موجودة أو لا تحتوي محاضرات.")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "rename_lecture_select_lecture")
    keyboard = [[KeyboardButton(l)] for l in bot_files[selected_subject].keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("✏️ اختر المحاضرة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def rename_lecture_newname(update, context, selected_subject, selected_lecture, new_name):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or selected_lecture not in bot_files[selected_subject]:
        await update.message.reply_text("❌ المحاضرة غير موجودة.")
        return
    new_name = new_name.strip()
    if not new_name:
        await update.message.reply_text("❌ اسم غير صالح.")
        return
    if new_name in bot_files[selected_subject]:
        await update.message.reply_text("❌ توجد محاضرة بنفس الاسم.")
        return
    bot_files[selected_subject][new_name] = bot_files[selected_subject].pop(selected_lecture)
    save_bot_files(bot_files)
    await update.message.reply_text(f"✅ تم تغيير اسم المحاضرة إلى: {new_name}")
    await show_admin_panel(update, context)

async def rename_file_start(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد.")
        return
    enter_menu(context, "rename_file_select_subject")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("✏️ اختر المادة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def rename_file_select_lecture(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or not bot_files[selected_subject]:
        await update.message.reply_text("❌ المادة غير موجودة أو لا تحتوي محاضرات.")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "rename_file_select_lecture")
    keyboard = [[KeyboardButton(l)] for l in bot_files[selected_subject].keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("✏️ اختر المحاضرة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def rename_file_select_file(update, context, selected_subject, selected_lecture):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or selected_lecture not in bot_files[selected_subject]:
        await update.message.reply_text("❌ المحاضرة غير موجودة.")
        return
    context.user_data["selected_subject"] = selected_subject
    context.user_data["selected_lecture"] = selected_lecture
    enter_menu(context, "rename_file_select_file")
    files = bot_files[selected_subject][selected_lecture]
    keyboard = [[KeyboardButton(f)] for f in files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("✏️ اختر الملف:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def rename_file_newname(update, context, selected_subject, selected_lecture, selected_file, new_name):
    bot_files = load_bot_files()
    files = bot_files.get(selected_subject, {}).get(selected_lecture, {})
    if selected_file not in files:
        await update.message.reply_text("❌ الملف غير موجود.")
        return
    new_name = new_name.strip()
    if not new_name:
        await update.message.reply_text("❌ اسم غير صالح.")
        return
    if new_name in files:
        await update.message.reply_text("❌ يوجد ملف بنفس الاسم.")
        return
    files[new_name] = files.pop(selected_file)
    bot_files[selected_subject][selected_lecture] = files
    save_bot_files(bot_files)
    await update.message.reply_text(f"✅ تم تغيير اسم الملف إلى: {new_name}")
    await show_admin_panel(update, context)

# ------------------ الحذف مع التأكيد ------------------
async def delete_subject_start(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد.")
        return
    enter_menu(context, "delete_subject_select")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("🗑️ اختر المادة التي تريد حذفها:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_subject_confirm(update, context, selected_subject):
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "delete_subject_confirm")
    keyboard = [[KeyboardButton(CONFIRM_DELETE_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text(f"⚠️ سيتم حذف المادة '{selected_subject}' بجميع محاضراتها وملفاتها. هل أنت متأكد؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_lecture_start(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد.")
        return
    enter_menu(context, "delete_lecture_select_subject")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("🗑️ اختر المادة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_lecture_select_lecture(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or not bot_files[selected_subject]:
        await update.message.reply_text("❌ المادة غير موجودة أو لا تحتوي محاضرات.")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "delete_lecture_select_lecture")
    keyboard = [[KeyboardButton(l)] for l in bot_files[selected_subject].keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("🗑️ اختر المحاضرة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_lecture_confirm(update, context, selected_subject, selected_lecture):
    context.user_data["selected_lecture"] = selected_lecture
    enter_menu(context, "delete_lecture_confirm")
    keyboard = [[KeyboardButton(CONFIRM_DELETE_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text(f"⚠️ سيتم حذف محاضرة '{selected_lecture}' من مادة '{selected_subject}'. تأكيد؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_file_start(update, context):
    bot_files = load_bot_files()
    if not bot_files:
        await update.message.reply_text("❌ لا توجد مواد.")
        return
    enter_menu(context, "delete_file_select_subject")
    keyboard = [[KeyboardButton(s)] for s in bot_files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("🗑️ اختر المادة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_file_select_lecture(update, context, selected_subject):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or not bot_files[selected_subject]:
        await update.message.reply_text("❌ المادة غير موجودة أو لا تحتوي محاضرات.")
        return
    context.user_data["selected_subject"] = selected_subject
    enter_menu(context, "delete_file_select_lecture")
    keyboard = [[KeyboardButton(l)] for l in bot_files[selected_subject].keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("🗑️ اختر المحاضرة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_file_select_file(update, context, selected_subject, selected_lecture):
    bot_files = load_bot_files()
    if selected_subject not in bot_files or selected_lecture not in bot_files[selected_subject]:
        await update.message.reply_text("❌ المحاضرة غير موجودة.")
        return
    context.user_data["selected_lecture"] = selected_lecture
    enter_menu(context, "delete_file_select_file")
    files = bot_files[selected_subject][selected_lecture]
    keyboard = [[KeyboardButton(f)] for f in files.keys()]
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text("🗑️ اختر الملف:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def delete_file_confirm(update, context, selected_subject, selected_lecture, selected_file):
    context.user_data["selected_file"] = selected_file
    enter_menu(context, "delete_file_confirm")
    keyboard = [[KeyboardButton(CONFIRM_DELETE_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text(f"⚠️ سيتم حذف الملف '{selected_file}' من '{selected_subject} > {selected_lecture}'. تأكيد؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

# ------------------ إدارة الطلاب ------------------
def build_students_labels():
    code_map = get_code_map()
    items = []
    for c, n in sorted(code_map.items(), key=lambda kv: (kv[1], kv[0])):
        label = f"👤 {n} | {c}"
        items.append(label)
    return items

def parse_code_from_label(label):
    m = re.search(r"(\d+)$", label.strip())
    return m.group(1) if m else None

async def show_students_list_for_delete(update, context, page=0):
    enter_menu(context, "delete_student_list")
    labels = build_students_labels()
    total = len(labels)
    if total == 0:
        await update.message.reply_text("لا يوجد طلاب.")
        return
    start = page * STUDENTS_PAGE_SIZE
    end = start + STUDENTS_PAGE_SIZE
    page_items = labels[start:end]
    keyboard = [[KeyboardButton(x)] for x in page_items]
    nav = []
    if page > 0:
        nav.append(KeyboardButton(PREV_BTN))
    if end < total:
        nav.append(KeyboardButton(NEXT_BTN))
    if nav:
        keyboard.append(nav)
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    context.user_data["students_page"] = page
    await update.message.reply_text(f"🗑️ اختر الطالب للحذف (صفحة {page+1}/{((total-1)//STUDENTS_PAGE_SIZE)+1}):", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_students_list_for_edit(update, context, page=0):
    enter_menu(context, "edit_student_list")
    labels = build_students_labels()
    total = len(labels)
    if total == 0:
        await update.message.reply_text("لا يوجد طلاب.")
        return
    start = page * STUDENTS_PAGE_SIZE
    end = start + STUDENTS_PAGE_SIZE
    page_items = labels[start:end]
    keyboard = [[KeyboardButton(x)] for x in page_items]
    nav = []
    if page > 0:
        nav.append(KeyboardButton(PREV_BTN))
    if end < total:
        nav.append(KeyboardButton(NEXT_BTN))
    if nav:
        keyboard.append(nav)
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    context.user_data["students_page"] = page
    await update.message.reply_text(f"✏️ اختر الطالب للتعديل (صفحة {page+1}/{((total-1)//STUDENTS_PAGE_SIZE)+1}):", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_students_list_for_suspend(update, context, page=0):
    enter_menu(context, "suspend_student_list")
    labels = build_students_labels()
    total = len(labels)
    if total == 0:
        await update.message.reply_text("لا يوجد طلاب.")
        return
    start = page * STUDENTS_PAGE_SIZE
    end = start + STUDENTS_PAGE_SIZE
    page_items = labels[start:end]
    keyboard = [[KeyboardButton(x)] for x in page_items]
    nav = []
    if page > 0:
        nav.append(KeyboardButton(PREV_BTN))
    if end < total:
        nav.append(KeyboardButton(NEXT_BTN))
    if nav:
        keyboard.append(nav)
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    context.user_data["students_page"] = page
    await update.message.reply_text(f"⏸️ اختر الطالب لإدارة الإيقاف (صفحة {page+1}/{((total-1)//STUDENTS_PAGE_SIZE)+1}):", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

# ------------------ استيراد الأكواد CSV ------------------
async def import_codes_prompt(update, context):
    enter_menu(context, "import_codes_wait_file")
    keyboard = [[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("📥 ارفع ملف CSV بصيغة: code,name", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def handle_import_codes_file(update, context, document):
    if not document or not document.file_name.lower().endswith(".csv"):
        await update.message.reply_text("❌ يرجى رفع ملف CSV صحيح.")
        return
    file = await document.get_file()
    data = await file.download_as_bytearray()
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    codes = load_codes()
    existing = {}
    for it in codes:
        c = normalize_code(str(it.get("code", "")))
        if c:
            existing[c] = str(it.get("name", "")).strip()

    added = updated = skipped = 0
    for r in rows:
        code_raw = str(r.get("code", "")).strip()
        name = str(r.get("name", "")).strip()
        c = normalize_code(code_raw)
        if not c or not name:
            skipped += 1
            continue
        if c in existing:
            if existing[c] != name:
                existing[c] = name
                updated += 1
            else:
                skipped += 1
        else:
            existing[c] = name
            added += 1

    new_codes = [{"code": k, "name": v} for k, v in sorted(existing.items())]
    save_codes(new_codes)
    await update.message.reply_text(f"✅ تم الاستيراد:\n- مضاف: {added}\n- مُحدّث: {updated}\n- متخطى: {skipped}")
    await show_admin_panel(update, context)

# ------------------ بث إشعارات ------------------
async def admin_broadcast_start(update, context):
    if not can_admin(update.effective_user.id, "broadcast"):
        await update.message.reply_text("❌ ليس لديك صلاحية بث الإشعارات.")
        return
    enter_menu(context, "admin_broadcast_prompt")
    keyboard = [[KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("📢 أرسل الآن رسالة البث: نص فقط أو صورة/فيديو مع كابشن.", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def admin_broadcast_collect(update, context, msg):
    if not can_admin(update.effective_user.id, "broadcast"):
        await update.message.reply_text("❌ ليس لديك صلاحية بث الإشعارات.")
        return
    user_data = context.user_data
    text = (msg.caption or msg.text or "").strip() if msg else ""
    if msg.photo:
        file_id = msg.photo[-1].file_id
        user_data["broadcast_type"] = "photo"
        user_data["broadcast_photo_id"] = file_id
        user_data["broadcast_text"] = text
    elif msg.video:
        file_id = msg.video.file_id
        user_data["broadcast_type"] = "video"
        user_data["broadcast_video_id"] = file_id
        user_data["broadcast_text"] = text
    elif text:
        user_data["broadcast_type"] = "text"
        user_data["broadcast_text"] = text
    else:
        await update.message.reply_text("❌ لم يتم التقاط محتوى للإرسال. أعد المحاولة.")
        return
    enter_menu(context, "admin_broadcast_confirm")
    keyboard = [[KeyboardButton(CONFIRM_SEND_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
    await update.message.reply_text("📤 جاهز للإرسال. اضغط تأكيد للإرسال.", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def admin_broadcast_send(update, context):
    if not can_admin(update.effective_user.id, "broadcast"):
        await update.message.reply_text("❌ ليس لديك صلاحية بث الإشعارات.")
        return
    user_data = context.user_data
    btype = user_data.get("broadcast_type")
    text = user_data.get("broadcast_text", "")

    ids = set()
    if os.path.exists(ALL_USERS_FILE):
        with open(ALL_USERS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    ids.add(int(line))

    sent = 0
    fail = 0
    for uid in ids:
        try:
            if btype == "photo":
                await context.bot.send_photo(chat_id=uid, photo=user_data["broadcast_photo_id"], caption=text if text else None)
            elif btype == "video":
                await context.bot.send_video(chat_id=uid, video=user_data["broadcast_video_id"], caption=text if text else None)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
            continue

    for k in ["broadcast_type", "broadcast_text", "broadcast_photo_id", "broadcast_video_id"]:
        user_data.pop(k, None)

    await update.message.reply_text(f"✅ تم الإرسال إلى {sent} مستخدم. أخفق {fail}.")
    await show_admin_panel(update, context)

# ------------------ لوحة إدارة الأدمنز (للسوبر أدمن) ------------------
async def show_manage_admins_menu(update, context):
    enter_menu(context, "manage_admins")
    keyboard = [
        [KeyboardButton(LIST_ADMINS_BTN)],
        [KeyboardButton(ADD_ADMIN_BTN)],
        [KeyboardButton(EDIT_ADMIN_PERMS_BTN)],
        [KeyboardButton(DELETE_ADMIN_BTN)],
        [KeyboardButton(BACK_BTN)],
        [KeyboardButton(MAIN_BTN)]
    ]
    await update.message.reply_text("👑 إدارة الأدمنز:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def show_admins_list(update, context, page=0, purpose=None):
    # purpose: None/list only | "edit_perms" | "delete_admin"
    admins = get_admin_ids()
    total = len(admins)
    if total == 0:
        await update.message.reply_text("لا يوجد أدمنز حالياً.")
        return
    enter_menu(context, "list_admins" if purpose is None else ("edit_admin_perms_select" if purpose=="edit_perms" else "delete_admin_select"))
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = admins[start:end]
    keyboard = []
    for aid in page_items:
        label = admin_label(aid)
        keyboard.append([KeyboardButton(label)])
    nav = []
    if page > 0:
        nav.append(KeyboardButton(PREV_BTN))
    if end < total:
        nav.append(KeyboardButton(NEXT_BTN))
    if nav:
        keyboard.append(nav)
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    context.user_data["admins_page"] = page
    title = "📋 قائمة الأدمنز" if purpose is None else ("✏️ اختر الأدمن لتعديل صلاحياته" if purpose=="edit_perms" else "🗑️ اختر الأدمن لحذفه")
    await update.message.reply_text(f"{title} (صفحة {page+1}/{((total-1)//PAGE_SIZE)+1}):", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

def parse_admin_id_from_label(text):
    m = re.match(r"ID:(\d+)", text)
    return int(m.group(1)) if m else None

async def show_edit_admin_perms_menu(update, context, admin_id: int):
    if not admin_id:
        await update.message.reply_text("❌ لم يتم تحديد الأدمن.")
        return
    if admin_id == OWNER_ID:
        await update.message.reply_text("❌ لا يمكن تعديل صلاحيات المالك.")
        await show_manage_admins_menu(update, context)
        return
    ensure_admin_perms_entry(admin_id)
    perms = _admin_perms.get(str(admin_id), {})
    enter_menu(context, "edit_admin_perms")
    # بناء أزرار التبديل
    keyboard = []
    for key, label in PERM_KEYS:
        status = "✅" if perms.get(key, False) else "❌"
        keyboard.append([KeyboardButton(f"{label} {status}")])
    keyboard.append([KeyboardButton(BACK_BTN)])
    keyboard.append([KeyboardButton(MAIN_BTN)])
    await update.message.reply_text(f"✏️ تعديل صلاحيات الأدمن:\n{admin_label(admin_id)}", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

def toggle_perm_for_admin(admin_id: int, perm_label_text: str):
    # يطابق على PERM_KEYS
    for key, label in PERM_KEYS:
        if perm_label_text.startswith(label):
            ensure_admin_perms_entry(admin_id)
            current = _admin_perms[str(admin_id)].get(key, False)
            _admin_perms[str(admin_id)][key] = not current
            save_admin_perms()
            return key, not current
    return None, None

# ------------------ المنطق الرئيسي ------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_user_registry_from_update(update)
    msg = update.message
    text = msg.text.strip() if (msg and msg.text) else ""
    user_data = context.user_data

    # تتبع النشاط
    update_user_activity(user_id)

    # تسجيل دخول
    if not is_logged_in(user_id):
        if not text:
            await update.message.reply_text("من فضلك أدخل كود الطالب:")
            return
        user_code = normalize_code(text)
        if not user_code or not user_code.isdigit() or len(user_code) < 5:
            await update.message.reply_text("من فضلك اكتب كود الطالب بالأرقام فقط.")
            return
        # تحقق من الإيقاف
        suspension = is_code_suspended(user_code)
        if suspension:
            reason = suspension.get("reason", "بدون سبب مذكور")
            await update.message.reply_text(f"🚫 حسابك موقوف مؤقتًا.\nالسبب: {reason}\nللاستفسار يرجى مراسلة الإدارة.")
            return
        student = check_code(user_code)
        if student:
            if not student.get("name"):
                await update.message.reply_text("✅ الكود صحيح لكن لا يوجد اسم مسجّل لهذا الكود. رجاءً حدّث ملف الأكواد.")
                return
            log_user(user_id, student["name"], student_code=user_code)
            await update.message.reply_text(f"✅ تم التحقق من الكود بنجاح! مرحباً {student['name']} 🌟")
            await show_main_menu(update, context, user_id)
        else:
            await update.message.reply_text("❌ كود غير صحيح. حاول مرة أخرى:")
        return

    # أزرار التنقل
    if text in {MAIN_BTN, "⬅️ رجوع للقائمة الرئيسية"}:
        await show_main_menu(update, context, user_id)
        return
    if text == BACK_BTN:
        await go_back(update, context)
        return

    # إلغاء أثناء الرفع
    if user_data.get("current_menu") == "add_item_file" and text == CANCEL_UPLOAD_BTN:
        await show_main_menu(update, context, user_id)
        return

    # تسجيل الخروج
    if text == "🚪 تسجيل الخروج":
        logout_user(user_id)
        user_data.clear()
        await update.message.reply_text("🚪 تم تسجيل الخروج بنجاح.\nأدخل كود الطالب لتسجيل الدخول من جديد:", reply_markup=ReplyKeyboardRemove())
        return

    # بياناتي
    if text == "👤 بياناتي":
        await show_my_data(update, context)
        return
    if user_data.get("current_menu") == "my_data":
        if text in ["📆 الجدول الدراسي", "🕒 الغياب والحضور"]:
            await update.message.reply_text("❗ لم يتم إضافة هذه الميزة بعد")
        return

    # إرسال مقترح/شكوى (لكل المستخدمين)
    if text == SEND_SUGGEST_BTN:
        await user_suggest_start(update, context)
        return
    if user_data.get("current_menu") == "user_suggest_text" and text not in {CANCEL_ACTION_BTN, BACK_BTN, MAIN_BTN}:
        await user_suggest_confirm(update, context, text)
        return
    if user_data.get("current_menu") == "user_suggest_confirm":
        if text == CONFIRM_SEND_BTN:
            await user_suggest_send(update, context)
        elif text == CANCEL_ACTION_BTN:
            await show_main_menu(update, context, user_id)
        return

    # قائمة المحاضرات
    if text == "📚 المحاضرات":
        await show_subjects_menu(update, context)
        return
    if user_data.get("current_menu") == "view_subjects" and text not in {BACK_BTN, MAIN_BTN}:
        selected_subject = text.strip()
        bot_files = load_bot_files()
        if selected_subject not in bot_files:
            await update.message.reply_text("❌ هذه المادة غير موجودة!")
            return
        await show_lectures_menu(update, context, selected_subject)
        return
    if user_data.get("current_menu") == "view_lectures" and text not in {BACK_BTN, MAIN_BTN}:
        selected_lecture = text.strip()
        selected_subject = user_data.get("selected_subject")
        bot_files = load_bot_files()
        if (not selected_subject) or (selected_subject not in bot_files) or (selected_lecture not in bot_files[selected_subject]):
            await update.message.reply_text("❌ هذه المحاضرة غير موجودة!")
            return
        await show_files_menu(update, context, selected_subject, selected_lecture)
        return
    if user_data.get("current_menu") == "view_files" and text not in {BACK_BTN, MAIN_BTN}:
        selected_subject = user_data.get("selected_subject")
        selected_lecture = user_data.get("selected_lecture")
        bot_files = load_bot_files()
        files = bot_files.get(selected_subject, {}).get(selected_lecture, {})
        if text not in files:
            await update.message.reply_text("❌ الملف غير موجود! اختر من القائمة.")
            return
        file_id = files[text]
        await update.message.reply_document(document=file_id, filename=text)
        inc_download_count(selected_subject, selected_lecture, text)
        return

    # -------- خصائص الأدمن --------
    if is_admin(user_id):
        # لوحة الأدمن
        if text == ADMIN_PANEL_BTN:
            await show_admin_panel(update, context)
            return

        # إدارة الأدمنز (سوبر فقط)
        if is_super_admin(user_id):
            if text == MANAGE_ADMINS_BTN:
                await show_manage_admins_menu(update, context)
                return

            if user_data.get("current_menu") == "manage_admins":
                if text == LIST_ADMINS_BTN:
                    await show_admins_list(update, context, 0)
                    return
                if text == ADD_ADMIN_BTN:
                    enter_menu(context, "super_add_admin_method")
                    keyboard = [
                        [KeyboardButton(ADD_BY_ID_BTN)],
                        [KeyboardButton(ADD_BY_USERNAME_BTN)],
                        [KeyboardButton(ADD_BY_CONTACT_BTN)],
                        [KeyboardButton(BACK_BTN)],
                        [KeyboardButton(MAIN_BTN)]
                    ]
                    await update.message.reply_text("اختر طريقة إضافة الأدمن:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
                    return
                if text == EDIT_ADMIN_PERMS_BTN:
                    await show_admins_list(update, context, 0, purpose="edit_perms")
                    return
                if text == DELETE_ADMIN_BTN:
                    await show_admins_list(update, context, 0, purpose="delete_admin")
                    return

            # إضافة أدمن - الطرق المختلفة
            if user_data.get("current_menu") == "super_add_admin_method":
                if text == ADD_BY_ID_BTN:
                    enter_menu(context, "super_add_admin_id")
                    await update.message.reply_text("أدخل ID الأدمن الجديد (أرقام):", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
                    return
                if text == ADD_BY_USERNAME_BTN:
                    enter_menu(context, "super_add_admin_username")
                    await update.message.reply_text("أدخل Username بدون @:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
                    return
                if text == ADD_BY_CONTACT_BTN:
                    enter_menu(context, "super_add_admin_contact")
                    kb = ReplyKeyboardMarkup([
                        [KeyboardButton("📱 أرسل جهة الاتصال الآن", request_contact=True)],
                        [KeyboardButton(BACK_BTN)],
                        [KeyboardButton(MAIN_BTN)]
                    ], resize_keyboard=True)
                    await update.message.reply_text("أرسل جهة الاتصال الخاصة بالمرشح (إذا كان user_id متاحًا).", reply_markup=kb)
                    return

            if user_data.get("current_menu") == "super_add_admin_id" and text not in {BACK_BTN, MAIN_BTN}:
                if text.isdigit():
                    new_id = int(text)
                    if new_id == OWNER_ID:
                        await update.message.reply_text("المستخدم هو المالك بالفعل.")
                    else:
                        _admins_set.add(new_id)
                        save_admins()
                        ensure_admin_perms_entry(new_id)
                        await update.message.reply_text(f"✅ تم إضافة الأدمن: {admin_label(new_id)}")
                    await show_manage_admins_menu(update, context)
                else:
                    await update.message.reply_text("❌ أدخل ID رقمي صالح.")
                return

            if user_data.get("current_menu") == "super_add_admin_username" and text not in {BACK_BTN, MAIN_BTN}:
                uid = find_user_id_by_username(text)
                if uid:
                    if uid == OWNER_ID:
                        await update.message.reply_text("المستخدم هو المالك بالفعل.")
                    else:
                        _admins_set.add(uid)
                        save_admins()
                        ensure_admin_perms_entry(uid)
                        await update.message.reply_text(f"✅ تم إضافة الأدمن: {admin_label(uid)}")
                    await show_manage_admins_menu(update, context)
                else:
                    await update.message.reply_text("❌ لم يتم العثور على هذا المستخدم في سجل البوت. اطلب منه بدء محادثة مع البوت أو استخدم طريقة ID.")
                return

            if user_data.get("current_menu") == "super_add_admin_contact":
                if msg and msg.contact:
                    c = msg.contact
                    cid = getattr(c, "user_id", None)
                    if cid:
                        if cid == OWNER_ID:
                            await update.message.reply_text("المستخدم هو المالك بالفعل.")
                        else:
                            _admins_set.add(cid)
                            save_admins()
                            ensure_admin_perms_entry(cid)
                            await update.message.reply_text(f"✅ تم إضافة الأدمن: {admin_label(cid)}")
                        await show_manage_admins_menu(update, context)
                    else:
                        await update.message.reply_text("❌ هذه الجهة لا تحتوي user_id. اطلب من المرشح بدء محادثة مع البوت أو استخدم ID/Username.")
                else:
                    await update.message.reply_text("أرسل جهة اتصال صالحة أو ارجع للخلف.")
                return

            # اختيار أدمن لتعديل صلاحياته
            if user_data.get("current_menu") == "edit_admin_perms_select":
                page = user_data.get("admins_page", 0)
                if text == NEXT_BTN:
                    await show_admins_list(update, context, page + 1, purpose="edit_perms")
                    return
                if text == PREV_BTN and page > 0:
                    await show_admins_list(update, context, page - 1, purpose="edit_perms")
                    return
                aid = parse_admin_id_from_label(text)
                if aid:
                    if aid == OWNER_ID:
                        await update.message.reply_text("❌ لا يمكن تعديل صلاحيات المالك.")
                        return
                    context.user_data["selected_admin_id"] = aid
                    await show_edit_admin_perms_menu(update, context, aid)
                return

            # تعديل الصلاحيات (التبديل)
            if user_data.get("current_menu") == "edit_admin_perms":
                aid = context.user_data.get("selected_admin_id")
                if text in {BACK_BTN, MAIN_BTN}:
                    await show_manage_admins_menu(update, context)
                    return
                key, new_val = toggle_perm_for_admin(aid, text)
                if key is not None:
                    await show_edit_admin_perms_menu(update, context, aid)
                else:
                    await update.message.reply_text("اختر أحد الأسطر لتبديل حالته.")
                return

            # حذف أدمن
            if user_data.get("current_menu") == "delete_admin_select":
                page = user_data.get("admins_page", 0)
                if text == NEXT_BTN:
                    await show_admins_list(update, context, page + 1, purpose="delete_admin")
                    return
                if text == PREV_BTN and page > 0:
                    await show_admins_list(update, context, page - 1, purpose="delete_admin")
                    return
                aid = parse_admin_id_from_label(text)
                if aid:
                    if aid == OWNER_ID:
                        await update.message.reply_text("❌ لا يمكن حذف المالك.")
                        return
                    context.user_data["selected_admin_id"] = aid
                    enter_menu(context, "delete_admin_confirm")
                    keyboard = [[KeyboardButton(CONFIRM_DELETE_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
                    await update.message.reply_text(f"⚠️ حذف الأدمن:\n{admin_label(aid)}\nتأكيد؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
                return
            if user_data.get("current_menu") == "delete_admin_confirm":
                if text == CONFIRM_DELETE_BTN:
                    aid = context.user_data.get("selected_admin_id")
                    if aid and aid in _admins_set:
                        _admins_set.discard(aid)
                        save_admins()
                        # إزالة صلاحياته
                        if str(aid) in _admin_perms:
                            _admin_perms.pop(str(aid), None)
                            save_admin_perms()
                    await update.message.reply_text("✅ تم حذف الأدمن.")
                    await show_manage_admins_menu(update, context)
                elif text == CANCEL_ACTION_BTN:
                    await show_manage_admins_menu(update, context)
                return

        # إدارة المحتوى (صلاحية content)
        if text == "➕ إضافة مادة جديدة":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await show_add_subject_prompt(update, context)
            return

        if user_data.get("current_menu") == "add_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            new_subject = text.strip()
            if not new_subject:
                await update.message.reply_text("❌ أدخل اسم مادة صالح.")
                return
            bot_files = load_bot_files()
            if new_subject in bot_files:
                await update.message.reply_text("❌ المادة موجودة بالفعل!")
            else:
                bot_files[new_subject] = {}
                save_bot_files(bot_files)
                await update.message.reply_text(f"✅ تم إضافة المادة: {new_subject}")
            await show_admin_panel(update, context)
            return

        if text == "➕ إضافة محاضرة جديدة":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await show_add_lecture_select_subject(update, context)
            return

        if user_data.get("current_menu") == "add_lecture_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            selected_subject = text.strip()
            bot_files = load_bot_files()
            if selected_subject not in bot_files:
                await update.message.reply_text("❌ هذه المادة غير موجودة!")
                return
            await show_add_lecture_prompt_name(update, context, selected_subject)
            return

        if user_data.get("current_menu") == "add_lecture_name" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            new_lecture = text.strip()
            selected_subject = user_data.get("selected_subject")
            bot_files = load_bot_files()
            if not selected_subject or selected_subject not in bot_files:
                await update.message.reply_text("❌ حدث خطأ. أعد المحاولة.")
                await show_add_lecture_select_subject(update, context)
                return
            if not new_lecture:
                await update.message.reply_text("❌ أدخل اسم محاضرة صالح.")
                return
            if new_lecture in bot_files[selected_subject]:
                await update.message.reply_text("❌ المحاضرة موجودة بالفعل!")
            else:
                bot_files[selected_subject][new_lecture] = {}
                save_bot_files(bot_files)
                await update.message.reply_text(f"✅ تم إضافة المحاضرة: {new_lecture} في المادة: {selected_subject}")
            await show_admin_panel(update, context)
            return

        if text == "➕ إضافة عنصر جديد":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await show_add_item_select_subject(update, context)
            return

        if user_data.get("current_menu") == "add_item_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            selected_subject = text.strip()
            bot_files = load_bot_files()
            if selected_subject not in bot_files:
                await update.message.reply_text("❌ هذه المادة غير موجودة!")
                return
            await show_add_item_select_lecture(update, context, selected_subject)
            return

        if user_data.get("current_menu") == "add_item_lecture" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            selected_lecture = text.strip()
            selected_subject = user_data.get("selected_subject")
            bot_files = load_bot_files()
            if (not selected_subject) or (selected_subject not in bot_files) or (selected_lecture not in bot_files[selected_subject]):
                await update.message.reply_text("❌ المحاضرة غير موجودة!")
                return
            await show_add_item_prompt_file(update, context, selected_subject, selected_lecture)
            return

        if user_data.get("current_menu") == "add_item_file":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            file_obj = None
            if msg:
                file_obj = msg.document or msg.audio or msg.video
            if file_obj:
                if not is_allowed_upload(file_obj):
                    await update.message.reply_text("❌ نوع الملف أو حجمه غير مسموح. الأنواع: PDF/PPT/صوت/فيديو وحجم ≤ 50MB.")
                    return
                file_id = file_obj.file_id
                if hasattr(file_obj, "file_name") and file_obj.file_name:
                    file_name = file_obj.file_name
                else:
                    ss = user_data.get("selected_subject", "subject")
                    sl = user_data.get("selected_lecture", "lecture")
                    file_name = f"{ss}-{sl}-{file_id}"
                selected_subject = user_data.get("selected_subject")
                selected_lecture = user_data.get("selected_lecture")
                bot_files = load_bot_files()
                if selected_subject not in bot_files:
                    bot_files[selected_subject] = {}
                if selected_lecture not in bot_files[selected_subject]:
                    bot_files[selected_subject][selected_lecture] = {}
                if file_name in bot_files[selected_subject][selected_lecture]:
                    await update.message.reply_text("❌ يوجد ملف بنفس الاسم بالفعل.")
                    return
                bot_files[selected_subject][selected_lecture][file_name] = file_id
                save_bot_files(bot_files)
                await update.message.reply_text(f"✅ تم رفع الملف: {file_name}")
                await show_admin_panel(update, context)
            else:
                await update.message.reply_text("❌ لم يتم التعرف على أي ملف. أعد المحاولة أو استخدم زر إلغاء الرفع.")
            return

        if text == RENAME_MENU_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            enter_menu(context, "admin_rename_menu")
            keyboard = [[KeyboardButton(RENAME_SUBJECT_BTN)], [KeyboardButton(RENAME_LECTURE_BTN)], [KeyboardButton(RENAME_FILE_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text("اختر ما تريد إعادة تسميته:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if text == RENAME_SUBJECT_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_subject_start(update, context)
            return

        if user_data.get("current_menu") == "rename_subject_select" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            context.user_data["selected_subject"] = text.strip()
            enter_menu(context, "rename_subject_newname")
            await update.message.reply_text(f"✏️ أدخل الاسم الجديد للمادة '{text.strip()}':", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "rename_subject_newname" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_subject_newname(update, context, context.user_data.get("selected_subject"), text)
            return

        if text == RENAME_LECTURE_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_lecture_start(update, context)
            return

        if user_data.get("current_menu") == "rename_lecture_select_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_lecture_select_lecture(update, context, text.strip())
            return

        if user_data.get("current_menu") == "rename_lecture_select_lecture" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            context.user_data["selected_lecture"] = text.strip()
            enter_menu(context, "rename_lecture_newname")
            await update.message.reply_text(f"✏️ أدخل الاسم الجديد للمحاضرة '{text.strip()}':", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "rename_lecture_newname" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_lecture_newname(update, context, user_data.get("selected_subject"), user_data.get("selected_lecture"), text)
            return

        if text == RENAME_FILE_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_file_start(update, context)
            return

        if user_data.get("current_menu") == "rename_file_select_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_file_select_lecture(update, context, text.strip())
            return

        if user_data.get("current_menu") == "rename_file_select_lecture" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_file_select_file(update, context, user_data.get("selected_subject"), text.strip())
            return

        if user_data.get("current_menu") == "rename_file_select_file" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            context.user_data["selected_file"] = text.strip()
            enter_menu(context, "rename_file_newname")
            await update.message.reply_text(f"✏️ أدخل الاسم الجديد للملف '{text.strip()}':", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "rename_file_newname" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await rename_file_newname(update, context, user_data.get("selected_subject"), user_data.get("selected_lecture"), user_data.get("selected_file"), text)
            return

        # الحذف
        if text == DELETE_MENU_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            enter_menu(context, "admin_delete_menu")
            keyboard = [[KeyboardButton(DELETE_SUBJECT_BTN)], [KeyboardButton(DELETE_LECTURE_BTN)], [KeyboardButton(DELETE_FILE_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text("اختر ما تريد حذفه:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if text == DELETE_SUBJECT_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_subject_start(update, context)
            return

        if user_data.get("current_menu") == "delete_subject_select" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_subject_confirm(update, context, text.strip())
            return

        if user_data.get("current_menu") == "delete_subject_confirm":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            if text == CONFIRM_DELETE_BTN:
                selected_subject = user_data.get("selected_subject")
                bot_files = load_bot_files()
                if selected_subject in bot_files:
                    bot_files.pop(selected_subject, None)
                    save_bot_files(bot_files)
                await update.message.reply_text("✅ تم الحذف.")
                await show_admin_panel(update, context)
            elif text == CANCEL_ACTION_BTN:
                await show_admin_panel(update, context)
            return

        if text == DELETE_LECTURE_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_lecture_start(update, context)
            return

        if user_data.get("current_menu") == "delete_lecture_select_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_lecture_select_lecture(update, context, text.strip())
            return

        if user_data.get("current_menu") == "delete_lecture_select_lecture" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_lecture_confirm(update, context, user_data.get("selected_subject"), text.strip())
            return

        if user_data.get("current_menu") == "delete_lecture_confirm":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            if text == CONFIRM_DELETE_BTN:
                ss = user_data.get("selected_subject")
                sl = user_data.get("selected_lecture")
                bot_files = load_bot_files()
                if ss in bot_files and sl in bot_files[ss]:
                    bot_files[ss].pop(sl, None)
                    save_bot_files(bot_files)
                await update.message.reply_text("✅ تم الحذف.")
                await show_admin_panel(update, context)
            elif text == CANCEL_ACTION_BTN:
                await show_admin_panel(update, context)
            return

        if text == DELETE_FILE_BTN:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_file_start(update, context)
            return

        if user_data.get("current_menu") == "delete_file_select_subject" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_file_select_lecture(update, context, text.strip())
            return

        if user_data.get("current_menu") == "delete_file_select_lecture" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_file_select_file(update, context, user_data.get("selected_subject"), text.strip())
            return

        if user_data.get("current_menu") == "delete_file_select_file" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            await delete_file_confirm(update, context, user_data.get("selected_subject"), user_data.get("selected_lecture"), text.strip())
            return

        if user_data.get("current_menu") == "delete_file_confirm":
            if not can_admin(user_id, "content"):
                await update.message.reply_text("❌ ليس لديك صلاحية إدارة المحتوى.")
                return
            if text == CONFIRM_DELETE_BTN:
                ss = user_data.get("selected_subject")
                sl = user_data.get("selected_lecture")
                sf = user_data.get("selected_file")
                bot_files = load_bot_files()
                if ss in bot_files and sl in bot_files[ss] and sf in bot_files[ss][sl]:
                    bot_files[ss][sl].pop(sf, None)
                    save_bot_files(bot_files)
                await update.message.reply_text("✅ تم الحذف.")
                await show_admin_panel(update, context)
            elif text == CANCEL_ACTION_BTN:
                await show_admin_panel(update, context)
            return

        # إدارة الطلاب: إضافة
        if text == ADMIN_ADD_STUDENT_BTN:
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            enter_menu(context, "admin_add_student_code")
            await update.message.reply_text("🔢 أدخل كود الطالب:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "admin_add_student_code" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            code = normalize_code(text)
            if not code:
                await update.message.reply_text("❌ أدخل كود رقمي صالح.")
                return
            code_map = get_code_map()
            if code in code_map:
                await update.message.reply_text("❌ هذا الكود موجود بالفعل.")
                return
            context.user_data["new_student_code"] = code
            enter_menu(context, "admin_add_student_name")
            await update.message.reply_text("📝 أدخل اسم الطالب:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "admin_add_student_name" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            name = text.strip()
            if not name:
                await update.message.reply_text("❌ أدخل اسم صالح.")
                return
            code = context.user_data.get("new_student_code")
            codes = load_codes()
            codes.append({"code": code, "name": name})
            save_codes(codes)
            context.user_data.pop("new_student_code", None)
            await update.message.reply_text(f"✅ تم إضافة الطالب: {name} (كود: {code})")
            await show_admin_panel(update, context)
            return

        # حذف طالب
        if text == ADMIN_DELETE_STUDENT_BTN:
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            enter_menu(context, "admin_delete_student_method")
            keyboard = [[KeyboardButton(SELECT_BY_CODE_BTN)], [KeyboardButton(SELECT_FROM_LIST_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text("اختر طريقة حذف الطالب:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if user_data.get("current_menu") == "admin_delete_student_method":
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            if text == SELECT_BY_CODE_BTN:
                enter_menu(context, "admin_delete_student_enter_code")
                await update.message.reply_text("🔢 أدخل كود الطالب المراد حذفه:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            elif text == SELECT_FROM_LIST_BTN:
                await show_students_list_for_delete(update, context, 0)
            return

        if user_data.get("current_menu") == "admin_delete_student_enter_code" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            code = normalize_code(text)
            code_map = get_code_map()
            if code not in code_map:
                await update.message.reply_text("❌ الكود غير موجود.")
                return
            context.user_data["selected_student_code"] = code
            enter_menu(context, "admin_delete_student_confirm")
            keyboard = [[KeyboardButton(CONFIRM_DELETE_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text(f"⚠️ حذف الطالب '{code_map[code]}' (كود: {code}). تأكيد؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if user_data.get("current_menu") == "delete_student_list":
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            page = user_data.get("students_page", 0)
            if text == NEXT_BTN:
                await show_students_list_for_delete(update, context, page + 1)
                return
            if text == PREV_BTN and page > 0:
                await show_students_list_for_delete(update, context, page - 1)
                return
            code = parse_code_from_label(text)
            if code:
                code_map = get_code_map()
                if code in code_map:
                    context.user_data["selected_student_code"] = code
                    enter_menu(context, "admin_delete_student_confirm")
                    keyboard = [[KeyboardButton(CONFIRM_DELETE_BTN)], [KeyboardButton(CANCEL_ACTION_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
                    await update.message.reply_text(f"⚠️ حذف الطالب '{code_map[code]}' (كود: {code}). تأكيد؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
                else:
                    await update.message.reply_text("❌ لم يتم العثور على الطالب.")
            return

        if user_data.get("current_menu") == "admin_delete_student_confirm":
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            if text == CONFIRM_DELETE_BTN:
                code = user_data.get("selected_student_code")
                codes = load_codes()
                codes = [c for c in codes if normalize_code(str(c.get("code", ""))) != code]
                save_codes(codes)
                await update.message.reply_text("✅ تم حذف الطالب.")
                await show_admin_panel(update, context)
            elif text == CANCEL_ACTION_BTN:
                await show_admin_panel(update, context)
            return

        # تعديل طالب
        if text == ADMIN_EDIT_STUDENT_BTN:
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            enter_menu(context, "admin_edit_student_method")
            keyboard = [[KeyboardButton(SELECT_BY_CODE_BTN)], [KeyboardButton(SELECT_FROM_LIST_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text("اختر طريقة تعديل الطالب:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if user_data.get("current_menu") == "admin_edit_student_method":
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            if text == SELECT_BY_CODE_BTN:
                enter_menu(context, "admin_edit_student_enter_code")
                await update.message.reply_text("🔢 أدخل كود الطالب:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            elif text == SELECT_FROM_LIST_BTN:
                await show_students_list_for_edit(update, context, 0)
            return

        if user_data.get("current_menu") == "admin_edit_student_enter_code" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            code = normalize_code(text)
            code_map = get_code_map()
            if code not in code_map:
                await update.message.reply_text("❌ الكود غير موجود.")
                return
            context.user_data["selected_student_code"] = code
            enter_menu(context, "admin_edit_student_choose_field")
            keyboard = [[KeyboardButton(EDIT_NAME_BTN)], [KeyboardButton(EDIT_CODE_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text("اختر ما تريد تعديله:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if user_data.get("current_menu") == "edit_student_list":
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            page = user_data.get("students_page", 0)
            if text == NEXT_BTN:
                await show_students_list_for_edit(update, context, page + 1)
                return
            if text == PREV_BTN and page > 0:
                await show_students_list_for_edit(update, context, page - 1)
                return
            code = parse_code_from_label(text)
            if code:
                code_map = get_code_map()
                if code in code_map:
                    context.user_data["selected_student_code"] = code
                    enter_menu(context, "admin_edit_student_choose_field")
                    keyboard = [[KeyboardButton(EDIT_NAME_BTN)], [KeyboardButton(EDIT_CODE_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
                    await update.message.reply_text("اختر ما تريد تعديله:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
                else:
                    await update.message.reply_text("❌ لم يتم العثور على الطالب.")
            return

        if user_data.get("current_menu") == "admin_edit_student_choose_field":
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            if text == EDIT_NAME_BTN:
                enter_menu(context, "admin_edit_student_new_name")
                await update.message.reply_text("📝 أدخل الاسم الجديد:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            elif text == EDIT_CODE_BTN:
                enter_menu(context, "admin_edit_student_new_code")
                await update.message.reply_text("🔢 أدخل الكود الجديد:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "admin_edit_student_new_name" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            new_name = text.strip()
            if not new_name:
                await update.message.reply_text("❌ اسم غير صالح.")
                return
            code = user_data.get("selected_student_code")
            codes = load_codes()
            updated = False
            for it in codes:
                if normalize_code(str(it.get("code", ""))) == code:
                    it["name"] = new_name
                    updated = True
                    break
            if updated:
                save_codes(codes)
                await update.message.reply_text("✅ تم تحديث الاسم.")
            else:
                await update.message.reply_text("❌ لم يتم العثور على الطالب.")
            await show_admin_panel(update, context)
            return

        if user_data.get("current_menu") == "admin_edit_student_new_code" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "student_edit"):
                await update.message.reply_text("❌ ليس لديك صلاحية تعديل بيانات الطالب.")
                return
            new_code = normalize_code(text)
            if not new_code:
                await update.message.reply_text("❌ كود غير صالح.")
                return
            codes_map = get_code_map()
            if new_code in codes_map:
                await update.message.reply_text("❌ الكود الجديد مستخدم بالفعل.")
                return
            old_code = user_data.get("selected_student_code")
            codes = load_codes()
            updated = False
            for it in codes:
                if normalize_code(str(it.get("code", ""))) == old_code:
                    it["code"] = new_code
                    updated = True
                    break
            if updated:
                save_codes(codes)
                await update.message.reply_text(f"✅ تم تحديث الكود من {old_code} إلى {new_code}.")
            else:
                await update.message.reply_text("❌ لم يتم العثور على الطالب.")
            await show_admin_panel(update, context)
            return

        # إيقاف/إلغاء إيقاف
        if text == ADMIN_SUSPEND_STUDENT_BTN:
            if not can_admin(user_id, "suspend"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإيقاف/الإلغاء.")
                return
            enter_menu(context, "admin_suspend_method")
            keyboard = [[KeyboardButton(SELECT_BY_CODE_BTN)], [KeyboardButton(SELECT_FROM_LIST_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
            await update.message.reply_text("اختر طريقة اختيار الطالب:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return

        if user_data.get("current_menu") == "admin_suspend_method":
            if not can_admin(user_id, "suspend"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإيقاف/الإلغاء.")
                return
            if text == SELECT_BY_CODE_BTN:
                enter_menu(context, "admin_suspend_enter_code")
                await update.message.reply_text("🔢 أدخل كود الطالب:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            elif text == SELECT_FROM_LIST_BTN:
                await show_students_list_for_suspend(update, context, 0)
            return

        if user_data.get("current_menu") == "admin_suspend_enter_code" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "suspend"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإيقاف/الإلغاء.")
                return
            code = normalize_code(text)
            code_map = get_code_map()
            if code not in code_map:
                await update.message.reply_text("❌ الكود غير موجود.")
                return
            context.user_data["selected_student_code"] = code
            susp = is_code_suspended(code)
            if susp:
                enter_menu(context, "admin_unsuspend_confirm")
                keyboard = [[KeyboardButton(UNSUSPEND_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
                await update.message.reply_text(f"الحساب موقوف حالياً.\nالسبب: {susp.get('reason','')}\nهل تريد إلغاء الإيقاف؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            else:
                enter_menu(context, "admin_suspend_reason")
                await update.message.reply_text("✍️ أدخل سبب الإيقاف المؤقت:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
            return

        if user_data.get("current_menu") == "suspend_student_list":
            if not can_admin(user_id, "suspend"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإيقاف/الإلغاء.")
                return
            page = user_data.get("students_page", 0)
            if text == NEXT_BTN:
                await show_students_list_for_suspend(update, context, page + 1)
                return
            if text == PREV_BTN and page > 0:
                await show_students_list_for_suspend(update, context, page - 1)
                return
            code = parse_code_from_label(text)
            if code:
                code_map = get_code_map()
                if code in code_map:
                    context.user_data["selected_student_code"] = code
                    susp = is_code_suspended(code)
                    if susp:
                        enter_menu(context, "admin_unsuspend_confirm")
                        keyboard = [[KeyboardButton(UNSUSPEND_BTN)], [KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]]
                        await update.message.reply_text(f"الحساب موقوف حالياً.\nالسبب: {susp.get('reason','')}\nهل تريد إلغاء الإيقاف؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
                    else:
                        enter_menu(context, "admin_suspend_reason")
                        await update.message.reply_text("✍️ أدخل سبب الإيقاف المؤقت:", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BACK_BTN)], [KeyboardButton(MAIN_BTN)]], resize_keyboard=True))
                else:
                    await update.message.reply_text("❌ لم يتم العثور على الطالب.")
            return

        if user_data.get("current_menu") == "admin_suspend_reason" and text not in {BACK_BTN, MAIN_BTN}:
            if not can_admin(user_id, "suspend"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإيقاف/الإلغاء.")
                return
            reason = text.strip()
            if not reason:
                await update.message.reply_text("❌ يرجى كتابة سبب.")
                return
            code = user_data.get("selected_student_code")
            suspend_code(code, reason, user_id)
            await update.message.reply_text("✅ تم إيقاف الحساب مؤقتًا.")
            await show_admin_panel(update, context)
            return

        if user_data.get("current_menu") == "admin_unsuspend_confirm" and text == UNSUSPEND_BTN:
            if not can_admin(user_id, "suspend"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإيقاف/الإلغاء.")
                return
            code = user_data.get("selected_student_code")
            unsuspend_code(code)
            await update.message.reply_text("✅ تم إلغاء الإيقاف.")
            await show_admin_panel(update, context)
            return

        # استيراد أكواد
        if text == IMPORT_CODES_BTN:
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            await import_codes_prompt(update, context)
            return

        if user_data.get("current_menu") == "import_codes_wait_file":
            if not can_admin(user_id, "student_add_delete"):
                await update.message.reply_text("❌ ليس لديك صلاحية إضافة/حذف الطلاب.")
                return
            if msg and msg.document:
                await handle_import_codes_file(update, context, msg.document)
            else:
                await update.message.reply_text("📄 أرسل ملف CSV الآن أو ارجع للخلف.")
            return

        # الشكاوى (عرض)
        if text == ADMIN_VIEW_COMPLAINTS_BTN:
            if not can_admin(user_id, "complaints"):
                await update.message.reply_text("❌ ليس لديك صلاحية عرض الشكاوى.")
                return
            await show_admin_complaints_list(update, context, 0)
            return

        if user_data.get("current_menu") == "admin_complaints_list":
            if not can_admin(user_id, "complaints"):
                await update.message.reply_text("❌ ليس لديك صلاحية عرض الشكاوى.")
                return
            page = user_data.get("complaints_page", 0)
            if text == NEXT_BTN:
                await show_admin_complaints_list(update, context, page + 1)
                return
            if text == PREV_BTN and page > 0:
                await show_admin_complaints_list(update, context, page - 1)
                return
            m = re.match(r"^شكوى/اقتراح\s+#(\d+)$", text)
            if m:
                await show_admin_complaint_detail(update, context, int(m.group(1)))
                return

        # بث
        if text == BROADCAST_BTN:
            await admin_broadcast_start(update, context)
            return

        if user_data.get("current_menu") == "admin_broadcast_prompt":
            if not can_admin(user_id, "broadcast"):
                await update.message.reply_text("❌ ليس لديك صلاحية بث الإشعارات.")
                return
            if msg and (msg.text or msg.caption or msg.photo or msg.video):
                await admin_broadcast_collect(update, context, msg)
            else:
                await update.message.reply_text("أرسل نصًا أو صورة/فيديو مع كابشن.")
            return

        if user_data.get("current_menu") == "admin_broadcast_confirm":
            if not can_admin(user_id, "broadcast"):
                await update.message.reply_text("❌ ليس لديك صلاحية بث الإشعارات.")
                return
            if text == CONFIRM_SEND_BTN:
                await admin_broadcast_send(update, context)
            elif text == CANCEL_ACTION_BTN:
                for k in ["broadcast_type", "broadcast_text", "broadcast_photo_id", "broadcast_video_id"]:
                    user_data.pop(k, None)
                await show_admin_panel(update, context)
            return

        # إحصائيات
        if text == STATS_BTN:
            if not can_admin(user_id, "stats"):
                await update.message.reply_text("❌ ليس لديك صلاحية الإحصائيات.")
                return
            s = get_stats_summary()
            lines = [
                "📊 إحصائيات البوت:",
                f"- إجمالي المستخدمين: {s['total_users']}",
                f"- المستخدمون النشطون آخر 7 أيام: {s['active_7d']}",
                f"- إجمالي مرات التحميل: {s['downloads_total']}",
                f"- عدد الملفات التي تم تحميلها (مرة واحدة على الأقل): {s['files_with_downloads']}",
            ]
            if s["top_lines"]:
                lines.append("🏆 أكثر الملفات تحميلاً:")
                lines.extend(s["top_lines"])
            await update.message.reply_text("\n".join(lines))
            return

    # رد افتراضي
    await update.message.reply_text("من فضلك اختر من القوائم المتاحة أو استخدم أزرار التنقل.")

# ------------------ أوامر سريعة ------------------
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not can_admin(update.effective_user.id, "broadcast"):
        return
    await admin_broadcast_start(update, context)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not can_admin(update.effective_user.id, "stats"):
        return
    s = get_stats_summary()
    lines = [
        "📊 إحصائيات البوت:",
        f"- إجمالي المستخدمين: {s['total_users']}",
        f"- المستخدمون النشطون آخر 7 أيام: {s['active_7d']}",
        f"- إجمالي مرات التحميل: {s['downloads_total']}",
        f"- عدد الملفات التي تم تحميلها (مرة واحدة على الأقل): {s['files_with_downloads']}",
    ]
    if s["top_lines"]:
        lines.append("🏆 أكثر الملفات تحميلاً:")
        lines.extend(s["top_lines"])
    await update.message.reply_text("\n".join(lines))

# ------------------ معالجة الأخطاء ------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err_text = "".join(traceback.format_exception(None, context.error, context.error.__traceback__)) if context.error else "Unknown error"
    logging.exception("Unhandled exception: %s", err_text)
    note = f"⚠️ خطأ غير متوقع:\n{context.error}\n\nتفاصيل:\n{err_text[:1500]}"
    try:
        await context.bot.send_message(OWNER_ID, note)
    except Exception:
        pass

# ------------------ تشغيل ------------------
if __name__ == "__main__":
    # تحميل الأدمن والصلاحيات + إصلاح سجل الدخول
    load_admins()
    load_admin_perms()
    fix_logged_file()

    # يمكنك استخدام متغير بيئة للتوكن
    token_env = os.getenv("BOT_TOKEN")
    bot_token = token_env if token_env else TOKEN

    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 البوت يعمل الآن …")

    app.run_polling()
