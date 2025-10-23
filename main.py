from fastapi import FastAPI #, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import influxdb_client
from collections import defaultdict
from pathlib import Path
import os, time #, secrets, shutil, json
from influxdb_client.client.exceptions import InfluxDBError

#------cloud-------- (ถูกคอมเมนต์ออก)
INFLUX_URL = "https://us-east-1-1.aws.cloud2.influxdata.com"
INFLUX_TOKEN = "Xttrq8yiXo5GrzZ5p6J2AxzXKYDEniqO9_3fzD_3Zt9fAbalTW1Cbtjt-mjfb9TZuSa-mK8_Iovea-dyIegQ-A=="
INFLUX_ORG = "KinseiPlant" # <-- ใช้ค่านี้
INFLUX_BUCKET = "plant_data"

#-------website---------- (เปิดใช้งานส่วนนี้)
# INFLUX_URL = os.getenv("INFLUX_URL")
# INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
# INFLUX_ORG = os.getenv("INFLUX_ORG")
# # ใช้ "plant_data" เป็นค่า default หากไม่ได้ตั้งค่า Environment Variable
# INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "plant_data") 

BASE_DIR = Path(__file__).parent # Path ปัจจุบันของ main.py


# --- สร้าง FastAPI App ---
app = FastAPI()


# --- ตั้งค่า CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    # ตรวจสอบว่า INFLUX_TOKEN มีค่าหรือไม่
    if not INFLUX_TOKEN:
        return JSONResponse({"error": "INFLUX_TOKEN is not set in environment variables."}, status_code=500)
        
    try:
        # สมมติว่า INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET และ defaultdict ถูก import แล้ว
        client = influxdb_client.InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        query_api = client.query_api()
        
        # *** NOTE: ควรใช้ Flux Query ที่ละเอียดกว่านี้ เพื่อดึงข้อมูล plant ทุกรายการออกมา ***
        # แต่เพื่อการสาธิตการใช้คีย์ผสม จะใช้ Query เดิมก่อน
        query = f'''
            from(bucket: "{INFLUX_BUCKET}")
            |> range(start: -30d)
            |> filter(fn: (r) => r["_measurement"] == "plant_information")
            |> last()  
        '''

        # --- (ทางเลือก) แนะนำให้กรองที่ Query เพื่อประสิทธิภาพที่ดีกว่า ---
        # ถ้าต้องการกรองที่ต้นทาง (InfluxDB) ซึ่งเร็วกว่า ให้แก้ Query เป็นแบบนี้แทน:
        # query = f'''
        #     from(bucket: "{INFLUX_BUCKET}")
        #     |> range(start: -30d)
        #     |> filter(fn: (r) => r["_measurement"] == "plant_information")
        #     |> last()
        #     |> filter(fn: (r) => exists r.model and r.model != "") // กรองให้มี model และไม่เป็นค่าว่าง
        # '''
        # --- จบทางเลือก ---

        tables = query_api.query(query, org=INFLUX_ORG)
        client.close()

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
        # ตรวจสอบว่าได้ import JSONResponse มาแล้ว (เช่น from fastapi.responses import JSONResponse)
        return JSONResponse({"error": str(e)}, status_code=500)


# --- 4. ✨ API ใหม่: สำหรับดึงข้อมูลย้อนหลังเพื่อทำกราฟ (ปรับปรุงใหม่) ---
@app.get("/api/plant/{model_name}/history")
async def get_plant_history(model_name: str, range_hours: int = 6):
    """
    API สำหรับดึงข้อมูลย้อนหลังของ Plant ที่ระบุตามช่วงเวลา (ชั่วโมง)
    นำกลับเป็น dict grouped by sensor_name:
      { "sensorA": [ {time, field, value, unit}, ... ], ... }
    ถ้า point ใน Influx มี tag/column ชื่อ 'unit' จะถูกใส่ลงในแต่ละ record
    """
    if not INFLUX_TOKEN:
        return JSONResponse({"error": "INFLUX_TOKEN is not set in environment variables."}, status_code=500)

    try:
        client = influxdb_client.InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        query_api = client.query_api()

        query = f'''
            from(bucket: "{INFLUX_BUCKET}")
              |> range(start: -{range_hours}h)
              |> filter(fn: (r) => r["_measurement"] == "plant_information")
              |> filter(fn: (r) => r["model"] == "{model_name}")
              |> filter(fn: (r) => r["_field"] == "温度_℃" or r["_field"] == "開度_%")
              |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
              |> yield(name: "results")
        '''

        tables = query_api.query(query, org=INFLUX_ORG)
        client.close()

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

