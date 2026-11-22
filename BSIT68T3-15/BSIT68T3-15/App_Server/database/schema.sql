-- schema.sql : โครงสร้างฐานข้อมูล SQLite (readings.db)
-- สกัดจากฟังก์ชัน db() ใน app.py (ภาคผนวก ค.2) / ตารางที่ 5.1 ในเล่ม
-- ปกติโปรแกรม Flask สร้างตารางเหล่านี้เองอัตโนมัติเมื่อเริ่มทำงาน ไฟล์นี้ไว้อ้างอิงหรือสร้างด้วยมือ
-- เวลาทุกช่องเก็บเป็น Unix time (วินาที)

-- ค่าอุณหภูมิ/ความชื้นที่ ESP32 ส่งมาทุก 60 วินาที
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    temperature REAL,
    humidity REAL,
    ts REAL
);

-- สถานะรีเลย์ที่ต้องการ (1 = เปิด, 0 = ปิด) ที่ ESP32 มาดึงไปใช้
CREATE TABLE IF NOT EXISTS relays (
    device TEXT,
    ch INTEGER,
    state INTEGER,
    ts REAL,
    PRIMARY KEY (device, ch)
);

-- เวลาที่ต้องปิดน้ำเองของการรดน้ำแต่ละครั้ง
CREATE TABLE IF NOT EXISTS timers (
    device TEXT,
    ch INTEGER,
    off_at REAL,
    minutes INTEGER,
    PRIMARY KEY (device, ch)
);

-- ประวัติการเปิด-ปิดปั๊ม (source เช่น line, stop, timer, watchdog)
CREATE TABLE IF NOT EXISTS pump_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    state INTEGER,
    source TEXT
);

-- เวลาที่ ESP32 ติดต่อมาล่าสุด / สถานะรีเลย์ที่ ESP32 รายงาน / สถานะแจ้งเตือนขาดการเชื่อมต่อ
CREATE TABLE IF NOT EXISTS device_state (
    device TEXT PRIMARY KEY,
    last_seen REAL,
    reported TEXT,
    offline_notified INTEGER DEFAULT 0
);

-- ค่าตั้งค่าทั่วไป เช่น ผู้ใช้ LINE ล่าสุดที่จะส่งข้อความแจ้งเตือนไปหา
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE INDEX IF NOT EXISTS pump_log_ts ON pump_log (ts);
CREATE INDEX IF NOT EXISTS readings_ts ON readings (ts);
