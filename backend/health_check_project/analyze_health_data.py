import pymssql
from langchain_ollama import OllamaLLM
from langchain.prompts import PromptTemplate
from langchain.memory import ConversationBufferMemory
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
import PyPDF2
from docx import Document
from io import BytesIO
from datetime import datetime
import logging
import uvicorn
import os

# 初始化 FastAPI 應用
app = FastAPI()

# 設定允許的來源（根據環境變數動態配置）
# 在開發環境中，可以設為 ["*"]；在生產環境中，應指定具體域名
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
if ALLOWED_ORIGINS == ["*"]:
    # 如果允許所有來源，則不應允許攜帶憑證
    allow_credentials = False
else:
    allow_credentials = True

# 添加 CORS 中間件以允許前端跨域請求
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # 動態配置允許的來源
    allow_credentials=allow_credentials,  # 根據來源設定是否允許憑證
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # 明確指定允許的 HTTP 方法
    allow_headers=["*"],  # 允許所有標頭
)

# 初始化密碼加密工具
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 定義請求體結構（用於註冊）
class RegisterRequest(BaseModel):
    full_name: str
    gender: str
    birth_date: str  # 格式：YYYY-MM-DD
    id_number: str
    password: str
    phone_number: str
    email: str

# 定義請求體結構（用於登入）
class LoginRequest(BaseModel):
    id_number: str
    password: str

# 定義請求體結構（用於忘記密碼）
class ForgotPasswordRequest(BaseModel):
    id_number: str
    email: str

# 定義請求體結構（用於互動模式）
class InteractiveRequest(BaseModel):
    query: str

# 連接到 Azure SQL Database 的通用函數
def connect_to_db(db_config: Dict[str, Any]):
    """連接到 Azure SQL Database。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。

    Returns:
        pymssql.Connection: 資料庫連線物件。

    Raises:
        HTTPException: 如果連線失敗，拋出 HTTP 異常。
    """
    try:
        conn = pymssql.connect(
            server=db_config['server'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            login_timeout=30,
            charset='UTF-8'
        )
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"連線 Azure SQL Database 時發生錯誤: {str(e)}")

# 檢查身分證字號是否已存在
def check_id_number_exists(db_config: Dict[str, Any], id_number: str) -> bool:
    """檢查身分證字號是否已存在於資料庫中。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        id_number (str): 要檢查的身分證字號。

    Returns:
        bool: 如果身分證字號存在，返回 True；否則返回 False。
    """
    conn = connect_to_db(db_config)
    cursor = conn.cursor()
    query = "SELECT COUNT(*) FROM users WHERE id_number = %s AND id_number != ''"
    cursor.execute(query, (id_number,))
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

# 驗證身分證字號格式（簡單檢查，實際應用中應更嚴格）
def validate_id_number(id_number: str) -> bool:
    """驗證身分證字號格式。

    Args:
        id_number (str): 要驗證的身分證字號。

    Returns:
        bool: 如果格式正確，返回 True；否則返回 False。
    """
    if len(id_number) != 10:
        return False
    if not id_number[0].isalpha() or not id_number[1:].isdigit():
        return False
    return True

# 驗證手機號碼格式（簡單檢查）
def validate_phone_number(phone_number: str) -> bool:
    """驗證手機號碼格式。

    Args:
        phone_number (str): 要驗證的手機號碼。

    Returns:
        bool: 如果格式正確，返回 True；否則返回 False。
    """
    return len(phone_number) == 10 and phone_number.isdigit()

# 驗證電子郵件格式（簡單檢查）
def validate_email(email: str) -> bool:
    """驗證電子郵件格式。

    Args:
        email (str): 要驗證的電子郵件。

    Returns:
        bool: 如果格式正確，返回 True；否則返回 False。
    """
    return "@" in email and "." in email and len(email) <= 100

# 提取 PDF 文件的文本
def extract_text_from_pdf(file: BytesIO) -> str:
    """從 PDF 文件中提取文本。

    Args:
        file (BytesIO): PDF 文件的二進位資料。

    Returns:
        str: 提取的文本。

    Raises:
        HTTPException: 如果提取失敗，拋出 HTTP 異常。
    """
    try:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"無法提取 PDF 文件內容: {str(e)}")

# 提取 Word 文件的文本
def extract_text_from_docx(file: BytesIO) -> str:
    """從 Word 文件中提取文本。

    Args:
        file (BytesIO): Word 文件的二進位資料。

    Returns:
        str: 提取的文本。

    Raises:
        HTTPException: 如果提取失敗，拋出 HTTP 異常。
    """
    try:
        doc = Document(file)
        text = ""
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"
        return text
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"無法提取 Word 文件內容: {str(e)}")

# 根據 id_number 查找 id_number
def get_id_number(db_config: Dict[str, Any], id_number: str) -> Optional[str]:
    """根據身分證字號查找用戶。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        id_number (str): 要查找的身分證字號。

    Returns:
        Optional[str]: 如果找到，返回身分證字號；否則返回 None。

    Raises:
        HTTPException: 如果身分證字號格式不正確或查詢失敗，拋出 HTTP 異常。
    """
    if not validate_id_number(id_number):
        raise HTTPException(status_code=400, detail="身分證字號格式不正確，應為 1 個字母 + 9 個數字，例如 A123456789")
    try:
        conn = connect_to_db(db_config)
        cursor = conn.cursor()
        query = "SELECT id_number FROM users WHERE id_number = %s AND id_number != ''"
        cursor.execute(query, (id_number,))
        user = cursor.fetchone()
        conn.close()
        return user[0] if user else None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查詢身分證字號時發生錯誤: {str(e)}")

# 註冊新用戶
def register_user(db_config: Dict[str, Any], user_data: RegisterRequest):
    """註冊新用戶。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        user_data (RegisterRequest): 用戶註冊資料。

    Returns:
        dict: 包含成功訊息的字典。

    Raises:
        HTTPException: 如果驗證失敗或資料庫操作失敗，拋出 HTTP 異常。
    """
    if not validate_id_number(user_data.id_number):
        raise HTTPException(status_code=400, detail="身分證字號格式不正確，應為 1 個字母 + 9 個數字，例如 A123456789")
    if check_id_number_exists(db_config, user_data.id_number):
        raise HTTPException(status_code=400, detail="此身分證字號已註冊，請確認是否輸入錯誤")
    if len(user_data.password) < 6:
        raise HTTPException(status_code=400, detail="密碼長度必須至少為 6 個字元")
    if len(user_data.password) > 100:
        raise HTTPException(status_code=400, detail="密碼長度不得超過 100 個字元")
    if not validate_phone_number(user_data.phone_number):
        raise HTTPException(status_code=400, detail="手機號碼格式不正確，應為 10 位數字，例如 0912345678")
    if not validate_email(user_data.email):
        raise HTTPException(status_code=400, detail="電子郵件格式不正確，例如 user@example.com")
    if not user_data.full_name or len(user_data.full_name) > 100:
        raise HTTPException(status_code=400, detail="姓名必須提供且長度不得超過 100 個字元")
    if user_data.gender not in ["M", "F", "Other"]:
        raise HTTPException(status_code=400, detail="性別必須是 'M'、'F' 或 'Other'")
    try:
        datetime.strptime(user_data.birth_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="出生日期格式不正確，應為 YYYY-MM-DD")

    hashed_password = pwd_context.hash(user_data.password)

    try:
        conn = connect_to_db(db_config)
        cursor = conn.cursor()
        query = """
        INSERT INTO users (full_name, gender, birth_date, id_number, password, phone_number, email, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            user_data.full_name,
            user_data.gender,
            user_data.birth_date,
            user_data.id_number,
            hashed_password,
            user_data.phone_number,
            user_data.email,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        conn.close()
        return {"message": "註冊成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"註冊過程中發生錯誤: {str(e)}")

# 登入驗證
def login_user(db_config: Dict[str, Any], login_data: LoginRequest):
    """驗證用戶登入。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        login_data (LoginRequest): 登入資料。

    Returns:
        dict: 包含成功訊息和身分證字號的字典。

    Raises:
        HTTPException: 如果驗證失敗或資料庫操作失敗，拋出 HTTP 異常。
    """
    if not validate_id_number(login_data.id_number):
        raise HTTPException(status_code=400, detail="身分證字號格式不正確，應為 1 個字母 + 9 個數字，例如 A123456789")
    if not login_data.password or len(login_data.password) > 100:
        raise HTTPException(status_code=400, detail="密碼無效")

    try:
        conn = connect_to_db(db_config)
        cursor = conn.cursor()
        query = "SELECT id, password FROM users WHERE id_number = %s AND id_number != ''"
        cursor.execute(query, (login_data.id_number,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            raise HTTPException(status_code=401, detail="身分證字號不存在")

        stored_password = user[1]
        if not stored_password or not stored_password.startswith('$2b$'):
            raise HTTPException(status_code=500, detail=f"資料庫中的密碼格式無效，需聯繫管理員修復 (id_number: {login_data.id_number})")

        if not pwd_context.verify(login_data.password, stored_password):
            raise HTTPException(status_code=401, detail="密碼錯誤")

        return {"message": "登入成功", "id_number": login_data.id_number}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"登入過程中發生錯誤: {str(e)}")

# 忘記密碼功能
def forgot_password(db_config: Dict[str, Any], forgot_data: ForgotPasswordRequest):
    """重置用戶密碼。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        forgot_data (ForgotPasswordRequest): 忘記密碼的請求資料。

    Returns:
        dict: 包含成功訊息和臨時密碼的字典。

    Raises:
        HTTPException: 如果驗證失敗或資料庫操作失敗，拋出 HTTP 異常。
    """
    if not validate_id_number(forgot_data.id_number):
        raise HTTPException(status_code=400, detail="身分證字號格式不正確，應為 1 個字母 + 9 個數字，例如 A123456789")
    if not validate_email(forgot_data.email):
        raise HTTPException(status_code=400, detail="電子郵件格式不正確，例如 user@example.com")

    try:
        conn = connect_to_db(db_config)
        cursor = conn.cursor()
        query = "SELECT id FROM users WHERE id_number = %s AND email = %s"
        cursor.execute(query, (forgot_data.id_number, forgot_data.email))
        user = cursor.fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="身分證字號與電子郵件不匹配")

        temp_password = "temp123456"
        hashed_temp_password = pwd_context.hash(temp_password)

        query = "UPDATE users SET password = %s WHERE id = %s"
        cursor.execute(query, (hashed_temp_password, user[0]))
        conn.commit()
        conn.close()

        return {"message": "密碼已重置，請使用臨時密碼登入並更改密碼", "temp_password": temp_password}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置密碼時發生錯誤: {str(e)}")

# 上傳健檢資料文件
async def upload_health_check(db_config: Dict[str, Any], id_number: str, file: UploadFile):
    """上傳健檢資料文件。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        id_number (str): 用戶的身分證字號。
        file (UploadFile): 上傳的文件。

    Returns:
        dict: 包含成功訊息和提取文本的字典。

    Raises:
        HTTPException: 如果文件格式不支援、內容為空或資料庫操作失敗，拋出 HTTP 異常。
    """
    if file.content_type not in ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
        raise HTTPException(status_code=400, detail="僅支援 PDF 和 Word (.docx) 文件")

    file_data = await file.read()
    file_extension = file.filename.split('.')[-1].lower()
    file_stream = BytesIO(file_data)
    if file_extension == "pdf":
        extracted_text = extract_text_from_pdf(file_stream)
    elif file_extension == "docx":
        extracted_text = extract_text_from_docx(file_stream)
    else:
        raise HTTPException(status_code=400, detail="不支援的文件格式")

    if not extracted_text.strip():
        raise HTTPException(status_code=400, detail="文件內容為空，無法提取有效文本")

    try:
        conn = connect_to_db(db_config)
        cursor = conn.cursor()

        query_check = "SELECT COUNT(*) FROM health_checks WHERE id_number = %s"
        cursor.execute(query_check, (id_number,))
        exists = cursor.fetchone()[0] > 0

        if exists:
            query = """
            UPDATE health_checks
            SET check_date = %s, data = %s, file_data = %s, extracted_text = %s
            WHERE id_number = %s
            """
            cursor.execute(query, (
                datetime.now().strftime("%Y-%m-%d"),
                extracted_text,
                file_data,
                extracted_text,
                id_number
            ))
            message = "健檢資料更新成功"
        else:
            query = """
            INSERT INTO health_checks (id_number, check_date, data, file_data, extracted_text, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (
                id_number,
                datetime.now().strftime("%Y-%m-%d"),
                extracted_text,
                file_data,
                extracted_text,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            message = "健檢資料上傳成功"

        conn.commit()
        conn.close()
        return {"message": message, "extracted_text": extracted_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上傳健檢資料時發生錯誤: {str(e)}")

# 從 Azure SQL Database 提取特定個人的健檢資料
def extract_health_data(db_config: Dict[str, Any], id_number: str) -> tuple[Optional[str], Optional[str]]:
    """從資料庫中提取特定個人的健檢資料。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        id_number (str): 用戶的身分證字號。

    Returns:
        tuple[Optional[str], Optional[str]]: 包含健檢資料和錯誤訊息的元組。
        如果成功，返回 (health_data, None)；如果失敗，返回 (None, error_message)。
    """
    try:
        conn = connect_to_db(db_config)
        cursor = conn.cursor()

        query = """
        SELECT u.full_name, u.gender, u.birth_date, hc.check_date, hc.extracted_text, hc.notes, u.created_at, u.id_number, u.phone_number, u.email
        FROM health_checks hc
        JOIN users u ON hc.id_number = u.id_number
        WHERE hc.id_number = %s
        ORDER BY hc.check_date DESC
        """
        cursor.execute(query, (id_number,))
        record = cursor.fetchone()
        conn.close()

        if not record:
            return None, f"找不到 id_number={id_number} 的健檢資料"

        health_data = f"使用者姓名: {record[0]}\n"
        health_data += f"性別: {record[1] if record[1] else '未提供'}\n"
        health_data += f"出生日期: {record[2]}\n"
        health_data += f"健檢日期: {record[3]}\n"
        health_data += f"健檢資料: {record[4] if record[4] else '無'}\n"
        health_data += f"建議: {record[5] if record[5] else '無'}\n"
        health_data += f"創建時間: {record[6] if record[6] else '未提供'}\n"
        health_data += f"身分證字號: {record[7]}\n"
        health_data += f"手機號碼: {record[8]}\n"
        health_data += f"電子郵件: {record[9]}\n"

        return health_data, None
    except Exception as e:
        return None, f"連線 Azure SQL Database 時發生錯誤: {str(e)}"

# 分析健檢資料
def analyze_health_data(db_config: Dict[str, Any], id_number: str) -> tuple[Dict[str, Any], Optional[OllamaLLM], Optional[ConversationBufferMemory], Optional[PromptTemplate]]:
    """分析健檢資料並提供建議。

    Args:
        db_config (Dict[str, Any]): 資料庫連線配置。
        id_number (str): 用戶的身分證字號。

    Returns:
        tuple: 包含分析結果、LLM 模型、記憶體和提示模板的元組。

    Raises:
        HTTPException: 如果資料提取或分析失敗，拋出 HTTP 異常。
    """
    memory = ConversationBufferMemory()

    llm = OllamaLLM(model="llama3:8b", base_url="http://localhost:11434", temperature=0.3)

    analysis_prompt_template = PromptTemplate(
        input_variables=["data"],
        template="你是一個專業的健康分析專家，請嚴格以繁體中文回答，不得使用英文或其他語言，所有醫學名詞必須使用繁體中文（例如使用「收縮壓」代替「systolic blood pressure」，使用「舒張壓」代替「diastolic blood pressure」）。請分析以下健檢資料並提供詳細建議：\n\n{data}\n\n請提供清晰的分析，包括每個指標是否正常（明確說明正常範圍，例如血壓正常範圍為90-120/60-80 mmHg，心率正常範圍為60-100 bpm），並給出至少三項具體的健康建議（例如飲食調整、運動建議、醫療檢查）和至少一項潛在疾病風險（例如高血壓可能導致心血管疾病）。所有回答必須是繁體中文。"
    )

    interactive_prompt_template = PromptTemplate(
        input_variables=["query"],
        template="你是一個專業的健康分析專家，請嚴格以繁體中文回答，不得使用英文或其他語言，所有醫學名詞必須使用繁體中文（例如使用「收縮壓」代替「systolic blood pressure」，使用「舒張壓」代替「diastolic blood pressure」）。基於之前的健檢資料和上下文，回答以下問題並提供新的具體建議：\n\n{query}\n\n請避免重複之前的回應，確保建議清晰實用，並提供至少三項具體行動建議和至少一項潛在疾病風險。所有回答必須是繁體中文。"
    )

    health_data, error = extract_health_data(db_config, id_number)
    if error:
        raise HTTPException(status_code=500, detail=error)

    memory.save_context({"input": "健檢資料"}, {"output": health_data})

    try:
        analysis_prompt = analysis_prompt_template.format(data=health_data)
        result = llm.invoke(analysis_prompt)
        return {
            "health_data": health_data,
            "analysis_result": result
        }, llm, memory, interactive_prompt_template
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析健檢資料時發生錯誤: {str(e)}")

# Azure SQL Database 連線配置
db_config = {
    'server': 'healthdbserver123.database.windows.net',
    'port': 1433,
    'user': 'bojay@healthdbserver123',  # 修正為正確的用戶名
    'password': '!Aa1085121',
    'database': 'health_db'
}

# API 端點：註冊新用戶
@app.post("/register")
async def register(request: RegisterRequest):
    logger.info(f"收到註冊請求: id_number={request.id_number}")
    try:
        result = register_user(db_config, request)
        logger.info(f"註冊成功: id_number={request.id_number}")
        return result
    except HTTPException as e:
        logger.error(f"註冊失敗: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"註冊時發生未知錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"註冊時發生未知錯誤: {str(e)}")

# API 端點：用戶登入
@app.post("/login")
async def login(request: LoginRequest):
    logger.info(f"收到登入請求: id_number={request.id_number}")  # 移除密碼記錄
    try:
        result = login_user(db_config, request)
        logger.info(f"登入成功: id_number={request.id_number}")
        return result
    except HTTPException as e:
        logger.error(f"登入失敗: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"登入時發生未知錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"登入時發生未知錯誤: {str(e)}")

# API 端點：忘記密碼
@app.post("/forgot-password")
async def forgot_password_endpoint(request: ForgotPasswordRequest):
    logger.info(f"收到忘記密碼請求: id_number={request.id_number}")
    try:
        result = forgot_password(db_config, request)
        logger.info(f"密碼重置成功: id_number={request.id_number}")
        return result
    except HTTPException as e:
        logger.error(f"密碼重置失敗: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"密碼重置時發生未知錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"密碼重置時發生未知錯誤: {str(e)}")

# API 端點：上傳健檢資料文件
@app.post("/health-check/upload/{id_number}")
async def upload_health_check_endpoint(id_number: str, file: UploadFile = File(...)):
    logger.info(f"收到上傳健檢資料請求: id_number={id_number}")
    if not validate_id_number(id_number):
        logger.error(f"身分證字號格式不正確: id_number={id_number}")
        raise HTTPException(status_code=400, detail="身分證字號格式不正確")
    id_number_exists = get_id_number(db_config, id_number)
    if id_number_exists is None:
        raise HTTPException(status_code=404, detail="身分證字號不存在")
    try:
        result = await upload_health_check(db_config, id_number, file)
        logger.info(f"上傳健檢資料成功: id_number={id_number}")
        return result
    except HTTPException as e:
        logger.error(f"上傳健檢資料失敗: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"上傳健檢資料時發生未知錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"上傳健檢資料時發生未知錯誤: {str(e)}")

# API 端點：user 角色，獲取健檢資料和分析結果
@app.get("/health-check/user/{id_number}")
async def get_user_health_check(id_number: str):
    logger.info(f"收到獲取健檢資料請求 (user 角色): id_number={id_number}")
    if not validate_id_number(id_number):
        logger.error(f"身分證字號格式不正確: id_number={id_number}")
        raise HTTPException(status_code=400, detail="身分證字號格式不正確")
    id_number_exists = get_id_number(db_config, id_number)
    if id_number_exists is None:
        raise HTTPException(status_code=404, detail="身分證字號不存在")
    try:
        result, _, _, _ = analyze_health_data(db_config, id_number)
        logger.info(f"獲取健檢資料成功 (user 角色): id_number={id_number}")
        return result
    except HTTPException as e:
        logger.error(f"獲取健檢資料失敗: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"獲取健檢資料時發生未知錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"獲取健檢資料時發生未知錯誤: {str(e)}")

# API 端點：other 角色，獲取健檢資料和分析結果，並支援互動模式
@app.get("/health-check/other/{id_number}")
async def get_other_health_check(id_number: str, request: Request):
    logger.info(f"收到獲取健檢資料請求 (other 角色): id_number={id_number}")
    if not validate_id_number(id_number):
        logger.error(f"身分證字號格式不正確: id_number={id_number}")
        raise HTTPException(status_code=400, detail="身分證字號格式不正確")
    id_number_exists = get_id_number(db_config, id_number)
    if id_number_exists is None:
        raise HTTPException(status_code=404, detail="身分證字號不存在")
    try:
        result, llm, memory, interactive_prompt_template = analyze_health_data(db_config, id_number)
        request.state.llm = llm
        request.state.memory = memory
        request.state.interactive_prompt_template = interactive_prompt_template
        logger.info(f"獲取健檢資料成功 (other 角色): id_number={id_number}")
        return result
    except HTTPException as e:
        logger.error(f"獲取健檢資料失敗: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"獲取健檢資料時發生未知錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"獲取健檢資料時發生未知錯誤: {str(e)}")

# API 端點：other 角色的互動模式
@app.post("/health-check/other/interact")
async def interact_health_check(request: Request, data: InteractiveRequest):
    logger.info("收到互動模式請求")
    if not hasattr(request.state, 'llm') or not hasattr(request.state, 'memory'):
        logger.error("互動模式未初始化")
        raise HTTPException(status_code=400, detail="請先呼叫 /health-check/other/{id_number} 來初始化互動模式")
    
    try:
        interactive_prompt = request.state.interactive_prompt_template.format(query=data.query)
        response = request.state.llm.invoke(interactive_prompt)
        logger.info("互動模式請求成功")
        return {"response": response}
    except Exception as e:
        logger.error(f"處理互動問題時發生錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"處理互動問題時發生錯誤: {str(e)}")

# 啟動伺服器
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)