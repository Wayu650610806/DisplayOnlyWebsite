from fastapi import FastAPI #, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import influxdb_client
from collections import defaultdict
from pathlib import Path
import os, time #, secrets, shutil, json
from influxdb_client.client.exceptions import InfluxDBError
from contextlib import asynccontextmanager

#------cloud-------- (ถูกคอมเมนต์ออก)
# INFLUX_URL = "https://us-east-1-1.aws.cloud2.influxdata.com"
# INFLUX_TOKEN = "Xttrq8yiXo5GrzZ5p6J2AxzXKYDEniqO9_3fzD_3Zt9fAbalTW1Cbtjt-mjfb9TZuSa-mK8_Iovea-dyIegQ-A=="
# INFLUX_ORG = "KinseiPlant" # <-- ใช้ค่านี้
# INFLUX_BUCKET = "plant_data"

#-------website---------- (เปิดใช้งานส่วนนี้)
INFLUX_URL = os.getenv("INFLUX_URL")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
# ใช้ "plant_data" เป็นค่า default หากไม่ได้ตั้งค่า Environment Variable
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "plant_data") 

BASE_DIR = Path(__file__).parent # Path ปัจจุบันของ main.py

# --- Global variable สำหรับเก็บ Client และ API ---
# เราจะกำหนดค่าใน lifespan
influx_client = None
influx_query_api = None

# --- Lifespan: โค้ดที่จะรันตอนแอปเปิดและปิด ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- โค้ดที่รันตอนแอปเริ่ม (Startup) ---
    global influx_client, influx_query_api
    if INFLUX_TOKEN:
        print("Connecting to InfluxDB...")
        influx_client = influxdb_client.InfluxDBClient(
            url=INFLUX_URL, 
            token=INFLUX_TOKEN, 
            org=INFLUX_ORG
        )
        influx_query_api = influx_client.query_api()
        print("InfluxDB connection established.")
    else:
        print("WARNING: INFLUX_TOKEN not set. InfluxDB client not created.")
    
    yield # <--- จุดนี้คือจุดที่แอปจะรันตามปกติ
    
    # --- โค้ดที่รันตอนแอปปิด (Shutdown) ---
    if influx_client:
        print("Closing InfluxDB connection...")
        influx_client.close()
        print("InfluxDB connection closed.")


# --- สร้าง FastAPI App โดยใช้ Lifespan ---
app = FastAPI(lifespan=lifespan)

# --- (ตั้งค่า CORS) ---
# สมมติหน้าเว็บของคุณคือ https://my-plant-app.onrender.com
origins = [
    "https://my-plant-app.onrender.com", # URL หน้าเว็บจริงบน Render
    "http://localhost",
    "http://localhost:8000", # สำหรับทดสอบ
    "http://127.0.0.1:8000"  # สำหรับทดสอบ
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # <-- ใช้ origins ที่กำหนด
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Mount โฟลเดอร์ static (สำหรับ CSS, JS, Fonts, logo.png ฯลฯ)
static_dir = "static"
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    # โค้ดนี้จะรันเมื่อโฟลเดอร์ static ไม่มีอยู่
    print(f"คำเตือน: ไม่พบโฟลเดอร์ '{static_dir}' สำหรับไฟล์ static")

# --- 2. สร้าง API สำหรับหน้า Home (/) ---
@app.get("/")
async def read_index():
    return FileResponse('index.html')

# --- 3. API หลักสำหรับดึงข้อมูล Plant ทั้งหมด ---
@app.get("/api/plants/overview")
async def get_plants_overview():
    # ตรวจสอบว่า Client พร้อมใช้งานหรือไม่ (ถูกสร้างใน lifespan แล้ว)
    if not influx_query_api:
        return JSONResponse({"error": "INFLUX_TOKEN is not set or client failed to initialize."}, status_code=500)
    
    try:
        query = f'''
            from(bucket: "{INFLUX_BUCKET}")
            |> range(start: -30d)
            |> filter(fn: (r) => r["_measurement"] == "plant_information")
            |> last()  
        '''
        
        # ใช้ query_api ที่สร้างไว้แล้ว (ไม่ต้องสร้างใหม่)
        tables = influx_query_api.query(query, org=INFLUX_ORG)
        
        # ไม่ต้อง client.close() ที่นี่

        plant_map = {}
        for table in tables:
            for record in table.records:
                
                # --- ✨ ส่วนที่แก้ไข: ตรวจสอบ model ก่อน ---
                # ดึงค่า model ออกมา
                model = record.values.get("model") 
                
                # ถ้า model เป็น None หรือ "" (สตริงว่าง) ให้ข้าม record นี้ไป
                if not model:
                    continue
                # --- จบส่วนที่แก้ไข ---

                # ถ้ามาถึงตรงนี้ได้ แปลว่า model มีค่าแน่นอน
                customer = record.values.get("customer") or "" # ป้องกัน customer เป็น None ด้วย
                province = record.values.get("province") or record.values.get("prefecture") or "" 
                
                # --- สร้างคีย์ผสม ---
                unique_key = (model, customer, province)
                
                # --- ใช้คีย์ผสมในการตรวจสอบและเพิ่มข้อมูล ---
                if unique_key not in plant_map:
                    plant_map[unique_key] = {
                        "customer": customer,
                        "province": province,
                        "model": model, # model จะมีค่าเสมอ
                        "last_updated": None, 
                        "sensors": defaultdict(dict)
                    }
                
                # ใช้คีย์ผสมในการอ้างอิงข้อมูล
                rec_time = record.get_time()
                if rec_time:
                    existing = plant_map[unique_key]["last_updated"]
                    if not existing or rec_time.isoformat() > existing:
                        plant_map[unique_key]["last_updated"] = rec_time.isoformat()
                        
                field = record.get_field()
                value = record.get_value()
                sensor_name = record.values.get("sensor_name")
                
                if field == "image_url":
                    plant_map[unique_key]["image_url"] = value
                elif sensor_name:
                    plant_map[unique_key]["sensors"][sensor_name][field] = value

        # --- ส่วนสุดท้าย: คืนค่าเป็น List ของ values (ข้อมูล Plant) ---
        return list(plant_map.values()) 
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# --- 4. ✨ API ใหม่: สำหรับดึงข้อมูลย้อนหลังเพื่อทำกราฟ (ปรับปรุงใหม่) ---
@app.get("/api/plant/{model_name}/history")
async def get_plant_history(model_name: str, range_hours: int = 6):
    
    if not influx_query_api:
        return JSONResponse({"error": "INFLUX_TOKEN is not set or client failed to initialize."}, status_code=500)

    try:
        query = f'''
            from(bucket: "{INFLUX_BUCKET}")
            |> range(start: -{range_hours}h)
            |> filter(fn: (r) => r["_measurement"] == "plant_information")
            |> filter(fn: (r) => r["model"] == "{model_name}")
            |> filter(fn: (r) => r["_field"] == "温度_℃" or r["_field"] == "開度_%")
            |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
            |> yield(name: "results")
        '''
        
        # ใช้ query_api ที่สร้างไว้แล้ว
        tables = influx_query_api.query(query, org=INFLUX_ORG)
        
        history_data = defaultdict(list)
        for table in tables:
            for record in table.records:
                # sensor_name ควรอยู่ใน tag values ตามตัวอย่างของคุณ
                sensor_name = record.values.get("sensor_name")
                if not sensor_name:
                    continue

                # พยายามอ่าน unit ถ้ามี (อาจเป็น tag หรือ column)
                unit = None
                # common places to find unit: custom tag 'unit', 'unit' in values, หรือ 'field_name' มี unit แยก
                if "unit" in record.values:
                    unit = record.values.get("unit")
                else:
                    # บางกรณี unit อาจอยู่ใน 'field_name' หรือใน _field เป็น "温度_℃"
                    # ถ้า _field มีสัญลักษณ์หน่วยให้ใช้นั้นเป็น fallback
                    _field = record.get_field() or ""
                    if "℃" in _field or "温度" in _field:
                        unit = "°C"
                    elif "%" in _field or "開度" in _field:
                        unit = "%"

                # เก็บ time เป็น ISO format, value เป็น numeric
                time_iso = None
                try:
                    t = record.get_time()
                    time_iso = t.isoformat() if t is not None else None
                except Exception:
                    time_iso = None

                history_data[sensor_name].append({
                    "time": time_iso,
                    "field": record.get_field(),
                    "value": record.get_value(),
                    "unit": unit
                })

        # (option) เรียงแต่ละ sensor ตามเวลา ascending เพื่อความแน่นอน
        for sensor, recs in history_data.items():
            recs.sort(key=lambda r: r.get("time") or "")

        return history_data

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    
@app.get("/detail.html")
async def read_detail():
    # ตรวจสอบว่ามีไฟล์ detail.html อยู่จริงหรือไม่ ก่อนที่จะส่งกลับไป
    if os.path.exists("detail.html"):
        return FileResponse("detail.html")
    return JSONResponse(status_code=404, content={"error": "detail.html not found"})

