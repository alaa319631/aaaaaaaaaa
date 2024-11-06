from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import sqlite3
import asyncio
from telegram import Bot

# تعريف التطبيق
app = FastAPI()

# إعداد القوالب
templates = Jinja2Templates(directory="templates")

# إعدادات تلغرام
TELEGRAM_API_TOKEN = '7731118993:AAE8-1Tc3xnjCvPOFUt59ldiK-4jnX888h0'
CHAT_ID = '6244988564'

def get_db_connection():
    conn = sqlite3.connect('school_database.db')
    return conn

def create_students_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY,
        name TEXT,
        age INTEGER,
        grade INTEGER
    )
    ''')
    conn.commit()
    conn.close()

create_students_table()

async def send_telegram_file(file_path: str):
    bot = Bot(token=TELEGRAM_API_TOKEN)
    with open(file_path, 'rb') as file:
        await bot.send_document(chat_id=CHAT_ID, document=file)

# معالجة عرض الصفحة في get
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# إضافة طالب جديد
@app.post("/students/")
async def add_student(name: str = Form(...), age: int = Form(...), grade: int = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO students (name, age, grade) VALUES (?, ?, ?)
        """,
        (name, age, grade)
    )
    conn.commit()
    conn.close()
    
    # إنشاء ملف للتعديل
    file_path = 'updated_students.txt'
    with open(file_path, 'w') as f:
        f.write(f"New student added: {name}, Age: {age}, Grade: {grade}")
    
    # إرسال الملف إلى تلغرام باستخدام async/await
    await send_telegram_file(file_path)
    
    return {"message": "Student added successfully"}

# جلب جميع الطلاب
@app.get("/students/")
async def get_all_students():
    conn = get_db_connection()
    cursor = conn.cursor()
    students = cursor.execute("SELECT * FROM students").fetchall()
    conn.close()
    return {"students": students}
